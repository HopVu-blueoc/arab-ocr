import json
from pathlib import Path

from sqlmodel import Session, select

from app.models import Batch, Image, Line


def _images(session: Session, batch_id: int) -> list[Image]:
    return session.exec(
        select(Image).where(Image.batch_id == batch_id).order_by(Image.filename)
    ).all()


def _lines(session: Session, image_id: int) -> list[Line]:
    return session.exec(
        select(Line).where(Line.image_id == image_id).order_by(Line.reading_order)
    ).all()


def _batch_slug(session: Session, batch_id: int) -> str:
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise LookupError(f"batch {batch_id} not found")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in batch.name)
    return f"{safe}-{batch_id}"


def export_jsonl(session: Session, batch_id: int, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_batch_slug(session, batch_id)}.jsonl"

    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for image in _images(session, batch_id):
            record = {
                "image_id": image.id,
                "filename": image.filename,
                "path": image.path,
                "width": image.width,
                "height": image.height,
                "status": str(image.status),
                "ocr_ms": image.ocr_ms,
                "lines": [
                    {
                        "reading_order": ln.reading_order,
                        "rec_text": ln.rec_text,
                        "corrected_text": ln.corrected_text,
                        "final_text": ln.final_text,
                        "score": ln.score,
                        "polygon": ln.polygon,
                        "status": str(ln.status),
                    }
                    for ln in _lines(session, image.id)
                ],
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def export_txt(session: Session, batch_id: int, out_dir: Path) -> Path:
    directory = out_dir / _batch_slug(session, batch_id)
    directory.mkdir(parents=True, exist_ok=True)

    for image in _images(session, batch_id):
        body = "\n".join(ln.final_text for ln in _lines(session, image.id))
        target = directory / f"{Path(image.filename).stem}.txt"
        target.write_text(body + "\n", encoding="utf-8", newline="\n")
    return directory
