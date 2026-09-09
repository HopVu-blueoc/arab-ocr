from pathlib import Path

from PIL import Image

from app.ocr.crops import Candidate
from app.ocr.engine import OcrLine, OcrResult, Polygon
from app.ocr.reading_order import sort_reading_order

DEFAULT_LINES = [
    OcrLine("مرحبا بالعالم", 0.98, [(60, 40), (600, 40), (600, 100), (60, 100)]),
    OcrLine("السطر", 0.95, [(440, 160), (820, 160), (820, 220), (440, 220)]),
    OcrLine("الثاني", 0.91, [(60, 160), (420, 160), (420, 220), (60, 220)]),
    OcrLine("اختبار التعرف الضوئي", 0.88, [(60, 280), (700, 280), (700, 340), (60, 340)]),
]

DEFAULT_RECOGNIZE_QUAD_RESULT = Candidate("يدوي", 0.93, "manual")


class FakeOcrEngine:
    """Deterministic engine for tests. Never imports paddle."""

    def __init__(
        self,
        lines: list[OcrLine] | None = None,
        recognize_quad_result: Candidate | None = DEFAULT_RECOGNIZE_QUAD_RESULT,
    ) -> None:
        self.lines = DEFAULT_LINES if lines is None else lines
        self.calls: list[Path] = []
        self.recognize_quad_result = recognize_quad_result
        self.recognize_quad_calls: list[Polygon] = []

    def warmup(self) -> None:
        """No model to build."""

    def run(self, image_path: Path) -> OcrResult:
        self.calls.append(image_path)
        with Image.open(image_path) as img:
            width, height = img.size
        return OcrResult(width=width, height=height, lines=sort_reading_order(self.lines))

    def recognize_quad(self, image, polygon: Polygon) -> Candidate | None:
        self.recognize_quad_calls.append(polygon)
        return self.recognize_quad_result
