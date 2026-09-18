import logging

from sqlmodel import Session

from app.config import get_settings
from app.db import session_scope
from app.dispatch import get_engine
from app.ocr.engine import OcrEngine
from app.reconcile import reconcile
from app.service import detect_box_for_image, run_ocr_for_image
from app.worker.celery_app import celery

logger = logging.getLogger(__name__)


def run_ocr(session: Session, image_id: int, engine: OcrEngine, generation: int = 0) -> None:
    """Task body, importable and testable without a broker."""
    run_ocr_for_image(session, image_id, engine, generation)


@celery.task(name="app.ocr_image", bind=True, max_retries=0)
def ocr_image(self, image_id: int, generation: int = 0) -> None:
    # generation=0 default: only reached by a task message enqueued by a
    # worker running before this argument existed. See claim_for_ocr.
    with session_scope() as session:
        run_ocr(session, image_id, get_engine(), generation)


@celery.task(name="app.detect_box")
def detect_box_task(image_id: int, polygon: list[list[float]]) -> dict:
    """Recognise a reviewer-drawn box. Runs in the worker so region OCR shares
    the GPU/model warmup that ocr_image already holds, instead of loading its
    own recognizers into the API process - see app/service.py:detect_box_for_image.
    """
    with session_scope() as session:
        return detect_box_for_image(session, image_id, polygon, get_engine())


@celery.task(name="app.reconcile_stuck_jobs")
def reconcile_stuck_jobs() -> dict[str, int]:
    """Periodic sweep for jobs the normal flow lost track of. See app/reconcile.py.

    Scheduled via celery.conf.beat_schedule; runs embedded in the worker
    process (docker-compose.yml's `-B` flag) since this deployment runs
    exactly one worker container. Scaling to multiple worker replicas would
    need beat split into its own service, or every replica would schedule
    the sweep redundantly - harmless (idempotent via the same atomic claim),
    just wasteful.
    """
    settings = get_settings()
    with session_scope() as session:
        counts = reconcile(
            session,
            enqueue_grace_seconds=settings.reconcile_enqueue_grace_seconds,
            running_timeout_seconds=settings.reconcile_running_timeout_seconds,
        )
    if counts["republished"] or counts["reassigned"]:
        logger.warning("reconcile sweep: %s", counts)
    return counts
