from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.config import get_settings
from app.db import SessionDep
from app.dispatch import enqueue_image
from app.exporters import export_jsonl, export_txt
from app.models import Batch, Image, ImageStatus
from app.schemas import BatchCreate, BatchOut, ImageOut
from app.service import import_folder

router = APIRouter(prefix="/api/batches", tags=["batches"])


def _counts(session: Session, batch_id: int) -> dict[str, int]:
    rows = session.exec(
        select(Image.status, func.count(Image.id))
        .where(Image.batch_id == batch_id)
        .group_by(Image.status)
    ).all()
    by_status = {s: n for s, n in rows}
    return {
        "image_count": sum(by_status.values()),
        "done_count": by_status.get(ImageStatus.done, 0),
        "approved_count": by_status.get(ImageStatus.approved, 0),
        "failed_count": by_status.get(ImageStatus.failed, 0),
    }


@router.post("", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
def create_batch(payload: BatchCreate, session: SessionDep) -> BatchOut:
    try:
        batch, imported, skipped = import_folder(session, payload.name, Path(payload.source_dir))
    except NotADirectoryError:
        raise HTTPException(
            status_code=400, detail=f"path does not exist: {payload.source_dir}"
        ) from None

    for image in session.exec(
        select(Image).where(Image.batch_id == batch.id, Image.status == ImageStatus.pending)
    ).all():
        image.status = ImageStatus.queued
        session.add(image)
    session.commit()

    for image_id in session.exec(select(Image.id).where(Image.batch_id == batch.id)).all():
        enqueue_image(image_id)

    counts = _counts(session, batch.id)
    counts.pop("image_count")
    return BatchOut(
        **batch.model_dump(),
        image_count=imported,
        skipped_count=skipped,
        **counts,
    )


@router.get("", response_model=list[BatchOut])
def list_batches(session: SessionDep) -> list[BatchOut]:
    return [
        BatchOut(**b.model_dump(), **_counts(session, b.id))
        for b in session.exec(select(Batch).order_by(Batch.created_at.desc())).all()
    ]


@router.get("/{batch_id}", response_model=BatchOut)
def get_batch(batch_id: int, session: SessionDep) -> BatchOut:
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return BatchOut(**batch.model_dump(), **_counts(session, batch_id))


@router.get("/{batch_id}/images", response_model=list[ImageOut])
def list_images(batch_id: int, session: SessionDep) -> list[Image]:
    return session.exec(
        select(Image).where(Image.batch_id == batch_id).order_by(Image.filename)
    ).all()


class ExportRequest(BaseModel):
    format: str  # jsonl | txt


@router.post("/{batch_id}/export")
def export_batch(
    batch_id: int, payload: ExportRequest, session: SessionDep
) -> dict[str, object]:
    if session.get(Batch, batch_id) is None:
        raise HTTPException(status_code=404, detail="batch not found")
    out_dir = get_settings().exports_dir
    if payload.format == "jsonl":
        path = export_jsonl(session, batch_id, out_dir)
    elif payload.format == "txt":
        path = export_txt(session, batch_id, out_dir)
    else:
        raise HTTPException(status_code=400, detail="format must be 'jsonl' or 'txt'")

    count = len(session.exec(select(Image.id).where(Image.batch_id == batch_id)).all())
    return {"path": str(path.resolve()), "count": count}
