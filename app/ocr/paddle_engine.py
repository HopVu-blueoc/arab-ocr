from pathlib import Path

from PIL import Image

from app.config import Settings, get_settings
from app.ocr.engine import OcrLine, OcrResult
from app.ocr.reading_order import sort_reading_order


class PaddleOcrEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._ocr = None

    def warmup(self) -> None:
        """Build the model. Call once per worker process."""
        from paddleocr import PaddleOCR

        if self._ocr is None:
            s = self.settings
            # lang/ocr_version are ignored while the model names are set; they are
            # passed anyway so blanking the model names falls back to lang defaults.
            self._ocr = PaddleOCR(
                lang=s.ocr_lang,
                ocr_version=s.ocr_version,
                text_recognition_model_name=s.ocr_rec_model,
                text_detection_model_name=s.ocr_det_model,
                device=s.ocr_device,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )

    def run(self, image_path: Path) -> OcrResult:
        self.warmup()
        results = self._ocr.predict(str(image_path))
        payload = results[0].json
        data = payload.get("res", payload)  # 3.x wraps the dict under "res"

        lines = [
            OcrLine(
                text=text,
                score=float(score),
                polygon=[(float(x), float(y)) for x, y in poly],
            )
            for text, score, poly in zip(
                data["rec_texts"], data["rec_scores"], data["rec_polys"], strict=True
            )
        ]
        with Image.open(image_path) as img:
            width, height = img.size
        return OcrResult(width=width, height=height, lines=sort_reading_order(lines))
