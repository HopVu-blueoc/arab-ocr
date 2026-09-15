import logging
import time

from sqlalchemy import update as sa_update
from sqlmodel import Session, select

from app.models import Image, ImageStatus, Line, utcnow
from app.ocr.engine import OcrEngine
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

    generation=0 is the legacy escape hatch for a task message that was
    already on the broker before this generation column existed - it skips
    the generation match (but still requires status='queued'), so an
    in-flight upgrade doesn't strand old messages. Never emitted by current
    code; only ever read.
    """
    conditions = [Image.id == image_id, Image.status == ImageStatus.queued]
    if generation:
        conditions.append(Image.ocr_generation == generation)

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
