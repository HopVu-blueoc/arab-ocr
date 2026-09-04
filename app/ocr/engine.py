from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

Polygon = list[tuple[float, float]]


@dataclass(frozen=True)
class OcrLine:
    text: str
    score: float
    polygon: Polygon  # 4 (x, y) points in source-image pixels


@dataclass(frozen=True)
class OcrResult:
    width: int
    height: int
    lines: list[OcrLine]  # already in reading order


@runtime_checkable
class OcrEngine(Protocol):
    def run(self, image_path: Path) -> OcrResult: ...

    def warmup(self) -> None:
        """Build any expensive state. Called once per Celery worker process."""


def bbox(polygon: Polygon) -> tuple[float, float, float, float]:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return min(xs), min(ys), max(xs), max(ys)
