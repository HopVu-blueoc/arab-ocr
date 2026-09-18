"""Smoke-test PaddleOCR-VL directly, no Celery/FastAPI in the way.

In the supported deployment this script runs in the CPU worker container and
calls the internal vLLM GPU service. Native mode is retained only for bounded
diagnosis of Paddle's local runtime.

Usage:
    docker compose -f docker-compose.yml -f docker-compose.paddle-vl.yml \
      exec worker python -u scripts/smoke_ocr_vl.py <image> [<image> ...]
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
    print("Connecting PaddleOCR-VL pipeline to its configured backend...")
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
