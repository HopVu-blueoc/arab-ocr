from app.config import get_settings
from app.ocr.engine import OcrEngine

_engine: OcrEngine | None = None


def get_engine() -> OcrEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        if settings.ocr_engine == "paddle_vl":
            from app.ocr.paddle_vl_engine import PaddleOcrVLEngine

            _engine = PaddleOcrVLEngine(settings)
        else:
            from app.ocr.paddle_engine import PaddleOcrEngine

            _engine = PaddleOcrEngine(settings)
    return _engine


def enqueue_image(image_id: int, generation: int) -> None:
    """Run OCR for one image. Inline today; Celery once JOB_BACKEND=celery.

    `generation` must match what the caller just wrote to Image.ocr_generation
    (build_image sets 1 on upload; retry_image increments it) - it is how a
    redelivered or duplicate task recognises it is stale. See
    app/service.py:claim_for_ocr.
    """
    if get_settings().job_backend == "celery":
        from app.worker.tasks import ocr_image

        ocr_image.delay(image_id, generation)
        return

    from app.db import session_scope
    from app.service import run_ocr_for_image

    with session_scope() as session:
        run_ocr_for_image(session, image_id, get_engine(), generation)
