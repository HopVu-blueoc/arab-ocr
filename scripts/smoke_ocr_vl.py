"""Smoke-test PaddleOCR-VL directly, no Celery/FastAPI in the way.

First run downloads the ~1B-param model (multiple GB) and will be slow on
this Mac's CPU - could be minutes per image. That is expected; this engine
is meant for the CUDA box. Use this script to sanity-check accuracy on a
handful of images before deciding whether to wait for CUDA or use it now.

Usage:
    uv run python -u scripts/smoke_ocr_vl.py <image> [<image> ...]
"""

import sys
import time
from pathlib import Path

from app.ocr.paddle_vl_engine import PaddleOcrVLEngine


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]]
    missing = [p for p in paths if not p.is_file()]
    if not paths or missing:
        print(__doc__)
        for p in missing:
            print(f"missing: {p}", file=sys.stderr)
        return 1

    engine = PaddleOcrVLEngine()
    print("Loading PaddleOCR-VL (first run downloads the model)...")
    engine.warmup()

    for path in paths:
        started = time.perf_counter()
        result = engine.run(path)
        elapsed = time.perf_counter() - started
        print(f"\n{path.name}  —  {len(result.lines)} lines, {elapsed:.1f}s")
        for line in result.lines:
            print(f"    {line.text}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
