"""Compare the old baseline settings against the current ones on real images.

Usage:
    uv run python scripts/bench_ocr.py <image> [<image> ...]

Prints, per image, the line count and mean confidence for each configuration
plus every line it read, so a change to the accuracy knobs can be judged on
your own corpus instead of on the synthetic fixture.
"""

import sys
import time
from pathlib import Path

from app.config import Settings
from app.ocr.paddle_engine import PaddleOcrEngine

BASELINE = {
    "ocr_det_model": "PP-OCRv5_mobile_det",
    "ocr_det_limit_side_len": None,
    "ocr_det_thresh": None,
    "ocr_det_box_thresh": None,
    "ocr_det_unclip_ratio": None,
    "ocr_min_box_side": 0,
    "ocr_pipeline_rec_score_thresh": 0.0,
    "ocr_rec_score_thresh": 0.0,
    "ocr_use_textline_orientation": False,
    "ocr_use_doc_orientation_classify": False,
    "ocr_use_doc_unwarping": False,
    "ocr_second_pass": False,
}


def run(label: str, settings: Settings, paths: list[Path]) -> None:
    engine = PaddleOcrEngine(settings)
    print(f"\n{'=' * 72}\n{label}\n{'=' * 72}")
    for path in paths:
        started = time.perf_counter()
        result = engine.run(path)
        elapsed = time.perf_counter() - started
        scores = [ln.score for ln in result.lines]
        mean = sum(scores) / len(scores) if scores else 0.0
        print(f"\n{path.name}  —  {len(result.lines)} lines, mean {mean:.3f}, {elapsed:.1f}s")
        for line in result.lines:
            tag = "" if line.source == "pipeline" else f"   [{line.source}]"
            print(f"    {line.score:.3f}  {line.text}{tag}")


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]]
    missing = [p for p in paths if not p.is_file()]
    if not paths or missing:
        print(__doc__)
        for p in missing:
            print(f"missing: {p}", file=sys.stderr)
        return 1

    run("BASELINE (paddle defaults, no orientation, no second pass)",
        Settings(**BASELINE), paths)
    run("CURRENT (.env settings)", Settings(), paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
