"""Crop geometry and image preparation for the second recognition pass.

Pure functions, no paddle import, so they stay unit-testable and fast.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from app.ocr.engine import Polygon

Point = tuple[float, float]


@dataclass(frozen=True)
class Candidate:
    """One recognition attempt for a single line."""

    text: str
    score: float
    source: str  # "pipeline" | "arabic-rescue" | "latin-rescue"


def order_quad(polygon: Polygon) -> list[Point]:
    """Return the four corners as (top-left, top-right, bottom-right, bottom-left).

    Uses the coordinate-sum/difference trick rather than sorting by y, which
    misorders quads that lean far enough for corners to swap rows.
    """
    pts = [(float(x), float(y)) for x, y in polygon]
    by_sum = sorted(pts, key=lambda p: p[0] + p[1])
    by_diff = sorted(pts, key=lambda p: p[0] - p[1])
    top_left, bottom_right = by_sum[0], by_sum[-1]
    top_right, bottom_left = by_diff[-1], by_diff[0]
    return [top_left, top_right, bottom_right, bottom_left]


def _distance(a: Point, b: Point) -> float:
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def warp_quad(image: np.ndarray, polygon: Polygon) -> np.ndarray:
    """Perspective-correct one detected quad into an upright rectangle.

    This is what rescues slanted text: the recogniser is trained on horizontal
    crops, and a sheared Arabic line smears its ligatures together far faster
    than Latin does. Sizing the target from the quad's own edge lengths keeps
    the glyph aspect ratio instead of stretching it.
    """
    top_left, top_right, bottom_right, bottom_left = order_quad(polygon)
    width = max(_distance(top_left, top_right), _distance(bottom_left, bottom_right))
    height = max(_distance(top_left, bottom_left), _distance(top_right, bottom_right))
    width, height = max(round(width), 1), max(round(height), 1)

    source = np.array([top_left, top_right, bottom_right, bottom_left], dtype=np.float32)
    target = np.array(
        [[0, 0], [width, 0], [width, height], [0, height]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(source, target)
    return cv2.warpPerspective(image, matrix, (width, height))


def preprocess_crop(
    crop: np.ndarray,
    *,
    target_height: int,
    max_upscale: float,
    clahe: bool,
) -> np.ndarray:
    """Upscale a small crop towards target_height and stretch local contrast.

    Only ever enlarges: shrinking a crop throws away the stroke detail the
    recogniser needs. The cap stops a 4px-tall detection from being blown up
    into pure interpolation noise.
    """
    height, width = crop.shape[:2]
    if height == 0 or width == 0:
        return crop

    scale = min(max(target_height / height, 1.0), max_upscale)
    if scale > 1.0:
        crop = cv2.resize(
            crop,
            (max(round(width * scale), 1), max(round(height * scale), 1)),
            interpolation=cv2.INTER_CUBIC,
        )

    if clahe and crop.ndim == 3:
        lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
        lightness, a, b = cv2.split(lab)
        equalised = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lightness)
        crop = cv2.cvtColor(cv2.merge((equalised, a, b)), cv2.COLOR_LAB2BGR)

    return crop


def crop_variants(crop: np.ndarray, *, tall_ratio: float = 1.5) -> list[np.ndarray]:
    """The crop, plus both 90-degree rotations when it looks like vertical text.

    A tall, narrow detection is usually a rotated sign. The recogniser cannot
    read it upright, and we do not know which way round it is, so both
    rotations become candidates and the score picks the winner.
    """
    height, width = crop.shape[:2]
    if width == 0 or height <= width * tall_ratio:
        return [crop]
    return [
        crop,
        cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE),
        cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE),
    ]


def pick_best(candidates: list[Candidate]) -> Candidate | None:
    """Highest-scoring non-blank candidate; the pipeline's own read wins ties."""
    ranked = [c for c in candidates if c.text.strip()]
    if not ranked:
        return None
    return max(ranked, key=lambda c: (c.score, c.source == "pipeline"))


def has_content(text: str) -> bool:
    """True when the string holds at least one letter or digit.

    The detector happily boxes specks of dirt, window mullions and sign
    borders; the recogniser turns those into "." or "-". Nothing without a
    letter or a digit is worth a reviewer's attention.
    """
    return any(ch.isalnum() for ch in text)


def quad_side_lengths(polygon: Polygon) -> tuple[float, float]:
    """(width, height) of a detected quad in source pixels."""
    top_left, top_right, bottom_right, bottom_left = order_quad(polygon)
    width = max(_distance(top_left, top_right), _distance(bottom_left, bottom_right))
    height = max(_distance(top_left, bottom_left), _distance(top_right, bottom_right))
    return width, height


def load_bgr(path) -> np.ndarray | None:
    """Read an image as BGR, tolerating non-ASCII filenames.

    cv2.imread goes through the C locale and returns None for unicode paths on
    some platforms; decoding the bytes ourselves does not.
    """
    try:
        buffer = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if buffer.size == 0:
        return None
    return cv2.imdecode(buffer, cv2.IMREAD_COLOR)
