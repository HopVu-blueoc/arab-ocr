from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.config import get_settings
from app.db import SessionDep
from app.dispatch import enqueue_image
from app.exporters import export_jsonl, export_txt
from app.models import Batch, Image, ImageStatus
from app.schemas import BatchCreate, BatchOut, ImageOut, UploadFailure, UploadResult
from app.storage import get_storage
from app.uploads import UploadRejected, display_name_for, register_image, store_upload

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


UploadFiles = Annotated[list[UploadFile], File()]


def _max_upload_bytes() -> int:
    return get_settings().max_upload_mb * (1 << 20)


@router.post("", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
def create_batch(payload: BatchCreate, session: SessionDep) -> BatchOut:
    """Create an empty batch. Images arrive separately, via POST .../images.

    Two commits because source_dir is derived from the row's own id, which
    only exists after the insert.
    """
    batch = Batch(name=payload.name, source_dir="")
    session.add(batch)
    session.commit()
    session.refresh(batch)

    batch.source_dir = f"batch-{batch.id}"  # the storage prefix, not a path
    session.add(batch)
    session.commit()
    session.refresh(batch)
    return BatchOut(**batch.model_dump(), **_counts(session, batch.id))


@router.post(
    "/{batch_id}/images",
    response_model=UploadResult,
    status_code=status.HTTP_201_CREATED,
)
def upload_images(batch_id: int, session: SessionDep, files: UploadFiles) -> UploadResult:
    """Store uploaded images and queue each new one for OCR.

    Per-file outcomes rather than a single status code: a 200-file upload must
    not be rejected wholesale because one file was a PDF.
    """
    if session.get(Batch, batch_id) is None:
        raise HTTPException(status_code=404, detail="batch not found")

    storage = get_storage()
    max_bytes = _max_upload_bytes()

    stored_images: list[Image] = []
    skipped = 0
    failed: list[UploadFailure] = []

    for upload in files:
        display_name = display_name_for(upload.filename)
        try:
            stored = store_upload(
                upload.file,
                display_name=display_name,
                batch_id=batch_id,
                storage=storage,
                max_bytes=max_bytes,
            )
        except UploadRejected as exc:
            failed.append(UploadFailure(filename=display_name, reason=str(exc)))
            continue

        image = register_image(session, batch_id=batch_id, stored=stored, storage=storage)
        if image is None:
            skipped += 1
            continue
        image.status = ImageStatus.queued
        stored_images.append(image)

    session.commit()

    # Enqueue exactly the rows this request created. Selecting every queued row
    # of the batch instead would re-enqueue anything an earlier upload left
    # queued, which under the Celery backend means OCRing it twice.
    for image in stored_images:
        enqueue_image(image.id)  # readable post-commit: the row refreshes on access

    return UploadResult(imported=len(stored_images), skipped=skipped, failed=failed)


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
def export_batch(batch_id: int, payload: ExportRequest, session: SessionDep) -> dict[str, object]:
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
