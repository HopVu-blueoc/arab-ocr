import logging
from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import case, tuple_
from sqlalchemy import delete as sa_delete
from sqlmodel import Session, func, select

from app.config import get_settings
from app.db import SessionDep
from app.dispatch import enqueue_image
from app.exporters import export_jsonl, export_txt
from app.models import Batch, Image, ImageStatus, Line
from app.pagination import decode_cursor, encode_cursor
from app.schemas import BatchCreate, BatchOut, ImageOut, Page, UploadFailure, UploadResult
from app.storage import get_storage
from app.storage.base import Storage
from app.uploads import (
    StoredUpload,
    UploadRejected,
    build_image,
    display_name_for,
    insert_or_existing,
    store_upload,
)

logger = logging.getLogger(__name__)


def _delete_quietly(storage: Storage, keys: list[str]) -> None:
    """Best-effort object cleanup. An orphaned object wastes space; a failure
    here must not fail a request whose rows are already committed."""
    for key in keys:
        try:
            storage.delete(key)
        except Exception:
            logger.warning("could not delete orphaned object %s", key, exc_info=True)


def _delete_quietly_many(storage: Storage, keys: list[str]) -> None:
    """Same contract as _delete_quietly, but one call for the whole chunk."""
    if not keys:
        return
    try:
        storage.delete_many(keys)
    except Exception:
        logger.warning("could not delete %d objects, falling back", len(keys), exc_info=True)
        _delete_quietly(storage, keys)

router = APIRouter(prefix="/api/batches", tags=["batches"])

BATCH_PAGE_DEFAULT = 50
BATCH_PAGE_MAX = 200
IMAGE_PAGE_DEFAULT = 100
IMAGE_PAGE_MAX = 500

# Rows deleted per transaction. Bounds both the open transaction and the
# number of rows held in memory when deleting a large batch.
_DELETE_CHUNK = 500

_ZERO_COUNTS = {"image_count": 0, "done_count": 0, "approved_count": 0, "failed_count": 0}


def _counts_for(session: Session, batch_ids: list[int]) -> dict[int, dict[str, int]]:
    """Status counts for several batches in one query.

    One aggregate for the whole page, not one per batch: the per-batch version
    made the cost of listing grow with the number of batches that had ever
    existed, which is what made this endpoint take 2.14s at 10k batches.
    """
    if not batch_ids:
        return {}

    rows = session.exec(
        select(
            Image.batch_id,
            func.count(Image.id),
            func.sum(case((Image.status == ImageStatus.done, 1), else_=0)),
            func.sum(case((Image.status == ImageStatus.approved, 1), else_=0)),
            func.sum(case((Image.status == ImageStatus.failed, 1), else_=0)),
        )
        .where(Image.batch_id.in_(batch_ids))
        .group_by(Image.batch_id)
    ).all()

    return {
        batch_id: {
            "image_count": total,
            "done_count": done,
            "approved_count": approved,
            "failed_count": failed,
        }
        for batch_id, total, done, approved, failed in rows
    }


def _counts(session: Session, batch_id: int) -> dict[str, int]:
    return _counts_for(session, [batch_id]).get(batch_id, _ZERO_COUNTS)


def _split_page(rows: list, limit: int, key: Callable[[object], dict]) -> tuple[list, str | None]:
    """Trim the lookahead row and turn it into the next cursor."""
    if len(rows) <= limit:
        return rows, None
    page = rows[:limit]
    return page, encode_cursor(key(page[-1]))


UploadFiles = Annotated[list[UploadFile], File()]


def _max_upload_bytes() -> int:
    return get_settings().max_upload_bytes


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
    # That SELECT opened a transaction. Close it before the storage writes
    # below, or SQLite holds it open across every network round trip and no
    # other writer - including the OCR worker - can commit meanwhile.
    session.rollback()

    storage = get_storage()
    max_bytes = _max_upload_bytes()

    # Phase A: all the slow work - decode, hash, upload - with no DB session
    # in play at all.
    stored_uploads: list[StoredUpload] = []
    failed: list[UploadFailure] = []
    seen_digests: set[str] = set()
    skipped = 0

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

        if stored.sha256 in seen_digests:
            # The same bytes twice in one request: identical content means an
            # identical key, so the second put() just overwrote the first.
            skipped += 1
            continue
        seen_digests.add(stored.sha256)
        stored_uploads.append(stored)

    # Phase B: one short write transaction, no I/O inside it.
    stored_images: list[Image] = []
    orphaned_keys: list[str] = []
    try:
        for stored in stored_uploads:
            image, inserted = insert_or_existing(session, build_image(batch_id, stored))
            if not inserted:
                skipped += 1
                # Identical bytes produce an identical key, so when the winner
                # already points at this key the put() was an idempotent
                # overwrite - deleting it would strand a live row.
                if image.path != stored.key:
                    orphaned_keys.append(stored.key)
                continue
            image.status = ImageStatus.queued
            stored_images.append(image)
        session.commit()
    except Exception:
        session.rollback()
        # Nothing was recorded, so every object this request wrote is garbage.
        _delete_quietly(storage, [s.key for s in stored_uploads])
        raise

    # Phase C: storage cleanup only after the rows are durable. Deleting while
    # the transaction could still roll back would remove bytes a live row
    # points at.
    _delete_quietly(storage, orphaned_keys)

    # Enqueue exactly the rows this request created. Selecting every queued row
    # of the batch instead would re-enqueue anything an earlier upload left
    # queued, which under the Celery backend means OCRing it twice.
    for image in stored_images:
        enqueue_image(image.id, image.ocr_generation)  # post-commit: refreshes on access

    return UploadResult(imported=len(stored_images), skipped=skipped, failed=failed)


@router.get("", response_model=Page[BatchOut])
def list_batches(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=BATCH_PAGE_MAX)] = BATCH_PAGE_DEFAULT,
    cursor: str | None = None,
) -> Page[BatchOut]:
    """Newest batches first, one keyset page at a time.

    `id` breaks ties on `created_at`: a bulk insert can land several batches in
    the same microsecond, and a non-unique sort key silently skips rows across
    a page boundary.
    """
    statement = select(Batch).order_by(Batch.created_at.desc(), Batch.id.desc())
    if cursor is not None:
        payload = decode_cursor(cursor, expected=("created_at", "id"))
        statement = statement.where(
            tuple_(Batch.created_at, Batch.id)
            < tuple_(datetime.fromisoformat(payload["created_at"]), payload["id"])
        )

    # One extra row answers "is there another page?" without a second query.
    rows = session.exec(statement.limit(limit + 1)).all()
    rows, next_cursor = _split_page(
        rows, limit, lambda b: {"created_at": b.created_at, "id": b.id}
    )

    counts = _counts_for(session, [b.id for b in rows])
    return Page(
        items=[
            BatchOut(**b.model_dump(), **counts.get(b.id, _ZERO_COUNTS)) for b in rows
        ],
        next_cursor=next_cursor,
    )


@router.get("/{batch_id}", response_model=BatchOut)
def get_batch(batch_id: int, session: SessionDep) -> BatchOut:
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return BatchOut(**batch.model_dump(), **_counts(session, batch_id))


@router.get("/{batch_id}/images", response_model=Page[ImageOut])
def list_images(
    batch_id: int,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=IMAGE_PAGE_MAX)] = IMAGE_PAGE_DEFAULT,
    cursor: str | None = None,
) -> Page[Image]:
    """One keyset page of a batch's images, by filename.

    `filename` is display text and explicitly not unique - uniqueness is on
    sha256 - so `id` has to break the tie or two images sharing a name would
    straddle a page boundary and one would be lost.
    """
    statement = (
        select(Image)
        .where(Image.batch_id == batch_id)
        .order_by(Image.filename, Image.id)
    )
    if cursor is not None:
        payload = decode_cursor(cursor, expected=("filename", "id"))
        statement = statement.where(
            tuple_(Image.filename, Image.id) > tuple_(payload["filename"], payload["id"])
        )

    rows = session.exec(statement.limit(limit + 1)).all()
    items, next_cursor = _split_page(
        rows, limit, lambda i: {"filename": i.filename, "id": i.id}
    )
    return Page(items=items, next_cursor=next_cursor)


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(batch_id: int, session: SessionDep) -> None:
    """Permanently delete a batch: its lines, images, stored objects, then itself.

    No ORM cascade is configured, so this is explicit and ordered for FK
    constraints: children before parents. An image mid-OCR when its batch is
    deleted needs no special handling - run_ocr_for_image already no-ops if
    the row is gone by the time the task runs.
    """
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    session.rollback()  # see upload_images: don't hold a transaction over I/O

    storage = get_storage()

    # A chunk at a time so neither the transaction nor the row set grows with
    # the batch: a 10k-image batch used to materialise every row and make one
    # storage call per image, all inside a single open write transaction.
    while True:
        rows = session.exec(
            select(Image.id, Image.path).where(Image.batch_id == batch_id).limit(_DELETE_CHUNK)
        ).all()
        if not rows:
            break

        image_ids = [row[0] for row in rows]
        keys = [row[1] for row in rows]

        # Bulk DELETEs rather than per-row session.delete(): no ORM
        # relationship() is declared, so the session would neither sequence
        # the children first nor avoid loading every row it deletes.
        session.exec(sa_delete(Line).where(Line.image_id.in_(image_ids)))
        session.exec(sa_delete(Image).where(Image.id.in_(image_ids)))
        session.commit()

        # Only once the rows are durably gone. The reverse order would delete
        # bytes that a rolled-back transaction still points a live row at.
        _delete_quietly_many(storage, keys)

    # The batch row goes last, so a crash part-way leaves the batch visible
    # and a repeated DELETE resumes where this one stopped.
    session.delete(batch)
    session.commit()


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

    count = session.exec(
        select(func.count()).select_from(Image).where(Image.batch_id == batch_id)
    ).one()
    return {"path": str(path.resolve()), "count": count}
