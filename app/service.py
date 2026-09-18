import logging
import time
from typing import Any

from sqlalchemy import update as sa_update
from sqlmodel import Session, select

from app.models import Image, ImageStatus, Line, utcnow
from app.ocr.crops import load_bgr
from app.ocr.engine import OcrEngine
from app.ocr.reading_order import reorder_lines
from app.storage import get_storage

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

logger = logging.getLogger(__name__)


def claim_for_ocr(session: Session, image_id: int, generation: int) -> Image | None:
    """Atomically claim image_id for OCR at this generation, or return None.

    The claim is one UPDATE ... WHERE status='queued' AND ocr_generation=?, so
    two things can never happen even under redelivery or a genuine duplicate
    worker pickup: reprocessing an image that already finished (status is no
    longer 'queued'), and two workers both believing they own the same run.
    Whichever request gets there first flips the row; every other claim
    attempt for that row matches zero rows and is a no-op, at the database
    level rather than by any check-then-act in Python.

    A same-generation message may also reclaim a row stuck at 'running', not
    only 'queued' - that is what lets Celery's own crash recovery work.
    task_reject_on_worker_lost redelivers a message only when Celery has
    detected the worker *process* actually died, so a same-generation
    redelivery finding 'running' means the previous attempt is dead, not
    merely slow; refusing to reclaim it here would leave the row stuck at
    'running' forever regardless of what celery_app.py's redelivery does.
    A message for a generation this row has already moved past (superseded
    by a retry, or by app/reconcile.py deciding the job was stuck and
    reassigning it) still cannot claim anything, queued or running.

    generation=0 is the legacy escape hatch for a task message that was
    already on the broker before this generation column existed - it only
    matches 'queued', not 'running' (there is no generation to compare, so
    there is no way to tell a genuine crash-redelivery from a live duplicate),
    so an in-flight upgrade doesn't strand old messages without also risking
    a double-claim. Never emitted by current code; only ever read.
    """
    conditions = [Image.id == image_id]
    if generation:
        conditions += [
            Image.ocr_generation == generation,
            Image.status.in_([ImageStatus.queued, ImageStatus.running]),
        ]
    else:
        conditions.append(Image.status == ImageStatus.queued)

    result = session.exec(
        sa_update(Image)
        .where(*conditions)
        .values(status=ImageStatus.running, updated_at=utcnow())
    )
    session.commit()

    if result.rowcount == 0:
        return None
    return session.get(Image, image_id)


def run_ocr_for_image(
    session: Session, image_id: int, engine: OcrEngine, generation: int = 0
) -> None:
    image = claim_for_ocr(session, image_id, generation)
    if image is None:
        logger.info(
            "skipping OCR for image %s generation %s: not claimable "
            "(already processed, superseded, or gone)",
            image_id,
            generation,
        )
        return

    started = time.perf_counter()
    try:
        # image.path is a storage key. as_local_path gives PaddleOCR the real
        # filesystem path it wants without app/ocr/ ever knowing about object
        # storage; for the local backend it is the file itself, no copy.
        with get_storage().as_local_path(image.path) as local_path:
            result = engine.run(local_path)
    except Exception as exc:  # noqa: BLE001 - the message is shown to the reviewer
        image.status = ImageStatus.failed
        image.error = f"{type(exc).__name__}: {exc}"
        image.updated_at = utcnow()
        session.commit()
        return

    for old in session.exec(select(Line).where(Line.image_id == image.id)).all():
        session.delete(old)

    for index, ocr_line in enumerate(result.lines):
        session.add(
            Line(
                image_id=image.id,
                reading_order=index,
                rec_text=ocr_line.text,
                score=ocr_line.score,
                polygon=[[float(x), float(y)] for x, y in ocr_line.polygon],
            )
        )

    image.width, image.height = result.width, result.height
    image.status = ImageStatus.done
    image.error = None
    image.ocr_ms = int((time.perf_counter() - started) * 1000)
    image.updated_at = utcnow()
    session.commit()  # one commit per image


def _line_payload(line: Line) -> dict[str, Any]:
    return {
        "id": line.id,
        "reading_order": line.reading_order,
        "rec_text": line.rec_text,
        "corrected_text": line.corrected_text,
        "final_text": line.final_text,
        "score": line.score,
        "polygon": line.polygon,
        "status": line.status.value,
    }


def detect_box_for_image(
    session: Session, image_id: int, polygon: list[list[float]], engine: OcrEngine
) -> dict[str, Any]:
    """Recognise one manually-drawn box and add it as a new Line.

    Shared by the synchronous (JOB_BACKEND=inline) request path and the
    Celery task (app/worker/tasks.py:detect_box_task) - the review's finding
    was that this used to run inference directly inside the API process
    (which has no GPU access and no serialization across request threads);
    moving it here, callable from a task, is what lets it run in the worker
    instead.

    Returns a plain, JSON-safe dict rather than raising HTTPException: a
    Celery task has no HTTP response to raise into. `status_code` is a hint
    the caller (the router, or the job-status endpoint) maps back onto a
    real response - it carries no FastAPI dependency itself.
    """
    image = session.get(Image, image_id)
    if image is None:
        return {"ok": False, "status_code": 404, "reason": "image not found"}

    if not hasattr(engine, "recognize_quad"):
        return {
            "ok": False,
            "status_code": 501,
            "reason": "manual box detection isn't supported with the paddle_vl engine",
        }

    with get_storage().as_local_path(image.path) as local_path:
        array = load_bgr(local_path)

    poly = [(float(x), float(y)) for x, y in polygon]
    candidate = engine.recognize_quad(array, poly)
    if candidate is None:
        return {"ok": False, "status_code": 422, "reason": "no text found in that region"}

    session.add(
        Line(
            image_id=image.id,
            reading_order=0,  # placeholder; overwritten by the re-sort below
            rec_text=candidate.text,
            score=candidate.score,
            polygon=[[x, y] for x, y in poly],
        )
    )
    session.commit()

    all_lines = session.exec(select(Line).where(Line.image_id == image.id)).all()
    for index, line in enumerate(reorder_lines(all_lines)):
        line.reading_order = index
        session.add(line)
    session.commit()

    ordered = session.exec(
        select(Line).where(Line.image_id == image.id).order_by(Line.reading_order)
    ).all()
    return {"ok": True, "status_code": 200, "lines": [_line_payload(ln) for ln in ordered]}
