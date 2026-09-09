from pathlib import Path

import numpy as np

from app.config import Settings, get_settings
from app.ocr.crops import (
    Candidate,
    crop_variants,
    has_content,
    load_bgr,
    pick_best,
    preprocess_crop,
    quad_side_lengths,
    warp_quad,
)
from app.ocr.engine import OcrLine, OcrResult, Polygon
from app.ocr.reading_order import sort_reading_order


class PaddleOcrEngine:
    """Two-stage engine tuned for accuracy over throughput.

    Stage 1 is PaddleOCR's OCR pipeline (detection + Arabic recognition, with
    orientation handling on). Stage 2 re-reads each detected line from a
    perspective-corrected, upscaled crop with both the Arabic and the Latin
    recogniser and keeps the best-scoring read. Stage 2 is what recovers
    slanted text and bilingual signs, where a single Arabic pass over a
    sheared crop scores poorly.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._ocr = None
        self._arabic_rec = None
        self._latin_rec = None

    # ---------------------------------------------------------------- setup
    def warmup(self) -> None:
        """Build every model. Call once per worker process."""
        from paddleocr import PaddleOCR

        s = self.settings
        if self._ocr is None:
            # No lang= or ocr_version=: PaddleOCR ignores both whenever
            # explicit model names are given, and passing them only produced a
            # UserWarning on every startup. The model names are the config.
            self._ocr = PaddleOCR(
                text_recognition_model_name=s.ocr_rec_model,
                text_detection_model_name=s.ocr_det_model,
                device=s.ocr_device,
                use_doc_orientation_classify=s.ocr_use_doc_orientation_classify,
                use_doc_unwarping=s.ocr_use_doc_unwarping,
                use_textline_orientation=s.ocr_use_textline_orientation,
                # A confirmed regression in paddlepaddle 3.3.1 + paddleocr
                # 3.7.0's oneDNN/PIR conversion path crashes CPU inference
                # with "NotImplementedError: ConvertPirAttribute2Runtime
                # Attribute not support [pir::ArrayAttribute<...>]" - this is
                # not this project's device, it's upstream
                # (github.com/PaddlePaddle/Paddle/issues/77340), reproduced
                # here in Docker and confirmed fixed by disabling oneDNN.
                enable_mkldnn=False,
            )
        if s.ocr_second_pass:
            if self._arabic_rec is None:
                self._arabic_rec = self._build_recognizer(s.ocr_rec_model)
            if self._latin_rec is None:
                self._latin_rec = self._build_recognizer(s.ocr_latin_rec_model)

    # ------------------------------------------------------------- stage 1
    def _detect_and_read(self, image_path: Path) -> list[OcrLine]:
        s = self.settings
        # Only pass the knobs that were actually set; None means "leave
        # PaddleOCR's own default alone".
        overrides = {
            "text_det_limit_side_len": s.ocr_det_limit_side_len,
            "text_det_thresh": s.ocr_det_thresh,
            "text_det_box_thresh": s.ocr_det_box_thresh,
            "text_det_unclip_ratio": s.ocr_det_unclip_ratio,
        }
        results = self._ocr.predict(
            str(image_path),
            text_rec_score_thresh=s.ocr_pipeline_rec_score_thresh,
            **{k: v for k, v in overrides.items() if v is not None},
        )
        payload = results[0].json
        data = payload.get("res", payload)  # 3.x wraps the dict under "res"

        return [
            OcrLine(
                text=text,
                score=float(score),
                polygon=[(float(x), float(y)) for x, y in poly],
            )
            for text, score, poly in zip(
                data["rec_texts"], data["rec_scores"], data["rec_polys"], strict=True
            )
        ]

    # ------------------------------------------------------------- stage 2
    def _read_crop(self, recogniser, crop: np.ndarray) -> tuple[str, float]:
        outputs = recogniser.predict(input=crop, batch_size=1)
        if not outputs:
            return "", 0.0
        payload = outputs[0].json
        data = payload.get("res", payload)
        return str(data.get("rec_text", "")), float(data.get("rec_score", 0.0))

    def _best_read(self, recogniser, variants: list[np.ndarray], source: str) -> Candidate:
        best = Candidate("", 0.0, source)
        for variant in variants:
            text, score = self._read_crop(recogniser, variant)
            if score > best.score:
                best = Candidate(text, score, source)
        return best

    def _build_recognizer(self, model_name: str):
        from paddleocr import TextRecognition

        return TextRecognition(model_name=model_name, device=self.settings.ocr_device, enable_mkldnn=False)

    def recognize_quad(self, image: np.ndarray, polygon: Polygon) -> Candidate | None:
        """Run both recognisers on one perspective-corrected, upscaled quad.

        Shared by the automatic rescue pass (`_rescue`, below) and the manual
        box-detection endpoint. A manual box has no prior pipeline read to
        compare against, so this returns the raw best candidate and leaves
        "is it good enough to use" to the caller. Builds the recognisers on
        first use regardless of `ocr_second_pass` - that setting only gates
        the automatic pipeline's second pass, not a reviewer's explicit
        request to recognize a region.
        """
        if self._arabic_rec is None:
            self._arabic_rec = self._build_recognizer(self.settings.ocr_rec_model)
        if self._latin_rec is None:
            self._latin_rec = self._build_recognizer(self.settings.ocr_latin_rec_model)

        crop = warp_quad(image, polygon)
        if crop.size == 0:
            return None

        prepared = preprocess_crop(
            crop,
            target_height=self.settings.ocr_crop_target_height,
            max_upscale=self.settings.ocr_crop_max_upscale,
            clahe=self.settings.ocr_crop_clahe,
        )
        variants = crop_variants(prepared)

        candidates = [
            self._best_read(self._arabic_rec, variants, "arabic-rescue"),
            self._best_read(self._latin_rec, variants, "latin-rescue"),
        ]
        return pick_best(candidates)

    def _rescue(self, image: np.ndarray, line: OcrLine) -> OcrLine:
        best = self.recognize_quad(image, line.polygon)
        if best is None:
            return line
        if best.score < line.score + self.settings.ocr_rescue_min_gain:
            return line
        return OcrLine(text=best.text, score=best.score, polygon=line.polygon, source=best.source)

    # ----------------------------------------------------------------- run
    def run(self, image_path: Path) -> OcrResult:
        self.warmup()
        s = self.settings

        lines = self._detect_and_read(image_path)

        image = load_bgr(image_path)
        if image is None:
            raise ValueError(f"could not decode image: {image_path}")

        # Drop specks before spending recognition passes on them.
        lines = [ln for ln in lines if min(quad_side_lengths(ln.polygon)) >= s.ocr_min_box_side]

        if s.ocr_second_pass and lines:
            lines = [
                self._rescue(image, line) if line.score <= s.ocr_second_pass_max_score else line
                for line in lines
            ]

        # Final quality gate, applied after the rescue so a weak first read
        # gets its second chance before being discarded.
        lines = [ln for ln in lines if ln.score >= s.ocr_rec_score_thresh and has_content(ln.text)]

        height, width = image.shape[:2]
        return OcrResult(width=width, height=height, lines=sort_reading_order(lines))
