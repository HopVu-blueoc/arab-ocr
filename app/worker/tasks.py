from sqlmodel import Session

from app.db import session_scope
from app.dispatch import get_engine
from app.ocr.engine import OcrEngine
from app.service import run_ocr_for_image
from app.worker.celery_app import celery


def run_ocr(session: Session, image_id: int, engine: OcrEngine, generation: int = 0) -> None:
    """Task body, importable and testable without a broker."""
    run_ocr_for_image(session, image_id, engine, generation)


@celery.task(name="app.ocr_image", bind=True, max_retries=0)
def ocr_image(self, image_id: int, generation: int = 0) -> None:
    # generation=0 default: only reached by a task message enqueued by a
    # worker running before this argument existed. See claim_for_ocr.
    with session_scope() as session:
        run_ocr(session, image_id, get_engine(), generation)
