from app.config import get_settings
from app.ocr.engine import OcrEngine

_engine: OcrEngine | None = None


def get_engine() -> OcrEngine:
    global _engine
    if _engine is None:
        from app.ocr.paddle_engine import PaddleOcrEngine

        _engine = PaddleOcrEngine()
    return _engine


def enqueue_image(image_id: int) -> None:
    """Run OCR for one image. Inline today; Celery once JOB_BACKEND=celery."""
    if get_settings().job_backend == "celery":
        from app.worker.tasks import ocr_image

        ocr_image.delay(image_id)
        return

    from app.db import session_scope
    from app.service import run_ocr_for_image

    with session_scope() as session:
        run_ocr_for_image(session, image_id, get_engine())
