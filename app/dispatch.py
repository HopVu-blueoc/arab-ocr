import logging
import time

from app.config import get_settings
from app.models import utcnow
from app.ocr.engine import OcrEngine

logger = logging.getLogger(__name__)

_engine: OcrEngine | None = None

_PUBLISH_ATTEMPTS = 3
_PUBLISH_RETRY_BASE_SECONDS = 0.5


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
    (build_image sets 1 on upload; retry_image increments it, as does
    app/reconcile.py reassigning a stuck job) - it is how a redelivered or
    duplicate task recognises it is stale. See app/service.py:claim_for_ocr.
    """
    if get_settings().job_backend == "celery":
        _publish_with_retry(image_id, generation)
        return

    from app.db import session_scope
    from app.service import run_ocr_for_image

    with session_scope() as session:
        run_ocr_for_image(session, image_id, get_engine(), generation)


def _publish_with_retry(image_id: int, generation: int) -> None:
    """Publish to Celery, retrying a transient broker failure a few times.

    On success, mark Image.enqueued_at - the durable signal that a message
    genuinely went out, which is what lets app/reconcile.py tell "never
    published" apart from "published, just waiting in a real backlog".

    Never raises. One file's publish failure must not fail an upload request
    for every other file that stored and enqueued fine - the row stays
    `queued` with enqueued_at unset, and the reconcile sweep picks it up.
    """
    from app.worker.tasks import ocr_image

    last_exc: Exception | None = None
    for attempt in range(_PUBLISH_ATTEMPTS):
        try:
            ocr_image.delay(image_id, generation)
        except Exception as exc:  # noqa: BLE001 - broker down, DNS blip, etc.
            last_exc = exc
            if attempt < _PUBLISH_ATTEMPTS - 1:
                time.sleep(_PUBLISH_RETRY_BASE_SECONDS * (attempt + 1))
            continue
        _mark_enqueued(image_id, generation)
        return

    logger.error(
        "could not publish OCR task for image %s generation %s after %d attempts: %s",
        image_id,
        generation,
        _PUBLISH_ATTEMPTS,
        last_exc,
    )


def _mark_enqueued(image_id: int, generation: int) -> None:
    from sqlalchemy import update as sa_update

    from app.db import session_scope
    from app.models import Image

    with session_scope() as session:
        session.exec(
            sa_update(Image)
            .where(Image.id == image_id, Image.ocr_generation == generation)
            .values(enqueued_at=utcnow())
        )
        session.commit()
