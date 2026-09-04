import hashlib
import time
from pathlib import Path

from PIL import Image as PILImage
from sqlmodel import Session, select

from app.models import Batch, Image, ImageStatus, Line, utcnow
from app.ocr.engine import OcrEngine

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_files(source: Path) -> list[Path]:
    if source.is_file():
        return [source] if source.suffix.lower() in IMAGE_SUFFIXES else []
    return [
        p for p in sorted(source.rglob("*")) if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    ]


def import_folder(session: Session, name: str, source_dir: Path) -> tuple[Batch, int, int]:
    """Returns (batch, imported_count, skipped_count). Skips by sha256.

    source_dir may be a directory (imported recursively) or a single image file.
    """
    if not source_dir.exists():
        raise NotADirectoryError(str(source_dir))

    batch = Batch(name=name, source_dir=str(source_dir))
    session.add(batch)
    session.commit()
    session.refresh(batch)

    imported = skipped = 0
    for path in _candidate_files(source_dir):
        digest = sha256_of(path)
        if session.exec(select(Image).where(Image.sha256 == digest)).first():
            skipped += 1
            continue
        with PILImage.open(path) as img:
            width, height = img.size
        session.add(
            Image(
                batch_id=batch.id,
                path=str(path),
                filename=path.name,
                sha256=digest,
                width=width,
                height=height,
                status=ImageStatus.pending,
            )
        )
        imported += 1
    session.commit()
    return batch, imported, skipped


def run_ocr_for_image(session: Session, image_id: int, engine: OcrEngine) -> None:
    image = session.get(Image, image_id)
    if image is None:
        return

    image.status = ImageStatus.running
    image.updated_at = utcnow()
    session.commit()

    started = time.perf_counter()
    try:
        result = engine.run(Path(image.path))
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
