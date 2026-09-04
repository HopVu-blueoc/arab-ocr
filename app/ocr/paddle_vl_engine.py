"""PaddleOCR-VL engine: a ~1B-parameter vision-language model instead of the
detector+CRNN pipeline in paddle_engine.py.

Verified against the installed paddleocr/paddlex source (3.7.0), not just
docs: with `use_layout_detection=False` and `prompt_label="spotting"`, the
pipeline sends the whole image to the VLM once and parses its generated
`<loc>` annotations into `spotting_res = {"rec_polys": [...], "rec_texts": [...]}`
- the same per-line-quad-plus-text shape as the classic OCR pipeline
(paddlex/inference/pipelines/paddleocr_vl/uilts.py:post_process_for_spotting).
That is what makes this pluggable behind the same OcrEngine protocol without
touching the box-linking UI, which depends on one polygon per line.

Not yet benchmarked on tests/data/pack1 - do that before trusting it on
customer images. Expect it to be much slower than the CNN pipeline on CPU;
it is a real fit for the CUDA box, not this Mac.

Score caveat: a generative model has no per-line softmax confidence the way
a CTC recognizer does. spotting_res carries no score, so every line here
gets 1.0. OCR_REC_SCORE_THRESH is effectively a no-op for this engine -
review every line manually rather than trusting the number.
"""

from pathlib import Path

from app.config import Settings, get_settings
from app.ocr.crops import has_content, load_bgr
from app.ocr.engine import OcrLine, OcrResult
from app.ocr.reading_order import sort_reading_order


def parse_spotting_result(payload: dict, *, width: int, height: int) -> list[OcrLine]:
    """Pure parsing step, independent of the model, so it is unit-testable
    with a hand-built payload instead of a multi-GB VLM.
    """
    data = payload.get("res", payload)
    spotting = data.get("spotting_res") or {}
    polys = spotting.get("rec_polys", [])
    texts = spotting.get("rec_texts", [])

    lines = [
        OcrLine(
            text=text,
            score=1.0,  # no native confidence; see module docstring
            polygon=[(float(x), float(y)) for x, y in poly],
            source="paddleocr-vl",
        )
        for poly, text in zip(polys, texts, strict=True)
        if has_content(text)
    ]
    return sort_reading_order(lines)


class PaddleOcrVLEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._pipeline = None

    def warmup(self) -> None:
        """Build the pipeline. Call once per worker process."""
        from paddleocr import PaddleOCRVL

        if self._pipeline is None:
            s = self.settings
            self._pipeline = PaddleOCRVL(
                vl_rec_model_name=s.ocr_vl_model_name,
                vl_rec_backend=s.ocr_vl_backend,
                use_layout_detection=False,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                device=s.ocr_device,
            )

    def run(self, image_path: Path) -> OcrResult:
        self.warmup()

        results = list(self._pipeline.predict(str(image_path), prompt_label="spotting"))
        payload = results[0].json

        image = load_bgr(image_path)
        if image is None:
            raise ValueError(f"could not decode image: {image_path}")
        height, width = image.shape[:2]

        lines = parse_spotting_result(payload, width=width, height=height)
        return OcrResult(width=width, height=height, lines=lines)
