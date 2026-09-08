import time

from sqlmodel import Session, select

from app.models import Image, ImageStatus, Line, utcnow
from app.ocr.engine import OcrEngine
from app.storage import get_storage

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def run_ocr_for_image(session: Session, image_id: int, engine: OcrEngine) -> None:
    image = session.get(Image, image_id)
    if image is None:
        return

    image.status = ImageStatus.running
    image.updated_at = utcnow()
    session.commit()

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
