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

The supported deployment uses a dedicated vLLM service. The worker remains a
CPU client and sends only the VLM recognition stage to the internal GPU
service; this avoids loading or duplicating the 0.9B model in API/Celery
processes. Native mode remains available for bounded diagnostics only.

Score caveat: a generative model has no per-line softmax confidence the way
a CTC recognizer does. spotting_res carries no score, so every line here
gets 1.0. OCR_REC_SCORE_THRESH is effectively a no-op for this engine -
review every line manually rather than trusting the number.
"""

import signal
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType

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
            kwargs = {
                "vl_rec_model_name": s.ocr_vl_model_name,
                "vl_rec_backend": s.ocr_vl_backend,
                "use_layout_detection": False,
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "device": s.ocr_device,
            }
            if s.ocr_vl_backend.endswith("-server"):
                if not s.ocr_vl_server_url:
                    raise ValueError(
                        "OCR_VL_SERVER_URL is required when OCR_VL_BACKEND "
                        f"is {s.ocr_vl_backend!r}"
                    )
                kwargs.update(
                    vl_rec_server_url=s.ocr_vl_server_url,
                    vl_rec_max_concurrency=s.ocr_vl_max_concurrency,
                )
            self._pipeline = PaddleOCRVL(**kwargs)

    def run(self, image_path: Path) -> OcrResult:
        self.warmup()

        with _request_timeout(self.settings.ocr_vl_request_timeout_seconds):
            results = list(self._pipeline.predict(str(image_path), prompt_label="spotting"))
        if not results:
            raise RuntimeError("PaddleOCR-VL returned no result")
        payload = results[0].json

        image = load_bgr(image_path)
        if image is None:
            raise ValueError(f"could not decode image: {image_path}")
        height, width = image.shape[:2]

        lines = parse_spotting_result(payload, width=width, height=height)
        return OcrResult(width=width, height=height, lines=lines)


@contextmanager
def _request_timeout(seconds: int) -> Iterator[None]:
    """Bound a VL service request in the Linux Celery child process.

    PaddleX does not expose an HTTP timeout on PaddleOCRVL. SIGALRM is safe
    here because each prefork child executes the task on its main thread and
    all supported application execution is inside Linux containers.
    """
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def raise_timeout(_signum: int, _frame: FrameType | None) -> None:
        raise TimeoutError(f"PaddleOCR-VL request exceeded {seconds} seconds")

    previous_handler = signal.signal(signal.SIGALRM, raise_timeout)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
