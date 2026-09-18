from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Column, Index, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class ImageStatus(StrEnum):
    pending = "pending"
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    approved = "approved"


class LineStatus(StrEnum):
    unreviewed = "unreviewed"
    approved = "approved"
    edited = "edited"


class Batch(SQLModel, table=True):
    __tablename__ = "batches"
    # The batch list pages on (created_at DESC, id DESC). A plain ASC index
    # serves a DESC scan - SQLite walks indexes in either direction.
    __table_args__ = (Index("ix_batches_created_at_id", "created_at", "id"),)

    id: int | None = Field(default=None, primary_key=True)
    name: str
    source_dir: str
    created_at: datetime = Field(default_factory=utcnow)


class Image(SQLModel, table=True):
    __tablename__ = "images"
    __table_args__ = (
        UniqueConstraint("sha256", name="uq_images_sha256"),
        # Covers the per-batch status aggregate behind the batch list. No
        # separate index on batch_id: this one's left prefix serves it.
        Index("ix_images_batch_id_status", "batch_id", "status"),
        # The image list pages on (filename ASC, id ASC) within one batch.
        Index("ix_images_batch_id_filename_id", "batch_id", "filename", "id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    batch_id: int = Field(foreign_key="batches.id")
    path: str
    filename: str
    # No index=True: uq_images_sha256 already creates one, and a second copy is
    # pure write amplification on every insert.
    sha256: str
    width: int
    height: int
    # No index=True: nothing filters status on its own - the only reader is the
    # aggregate above, which is served by ix_images_batch_id_status.
    status: ImageStatus = Field(default=ImageStatus.pending)
    # Bumped each time OCR is (re)dispatched for this image, and carried on the
    # queued task's arguments. A worker claims the job with an atomic
    # UPDATE ... WHERE status='queued' AND ocr_generation=:generation - a
    # redelivered or duplicate message for a stale generation or a
    # no-longer-queued image matches zero rows and is a no-op. This is what
    # stops a task that got redelivered after already completing (acks_late +
    # reject_on_worker_lost can do this on a killed worker) from re-running
    # OCR and deleting reviewer corrections. See app/service.py:claim_for_ocr.
    ocr_generation: int = Field(default=1)
    # Set only once dispatch.enqueue_image's Celery publish actually succeeds.
    # A row that is status='queued' with this still None past a short grace
    # period never got a message at all - see app/reconcile.py - which a
    # normal backlog (published, just waiting its turn) cannot produce.
    enqueued_at: datetime | None = Field(default=None)
    error: str | None = None
    ocr_ms: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Line(SQLModel, table=True):
    __tablename__ = "lines"
    # Every read of a line is "the lines of this image, in reading order".
    __table_args__ = (Index("ix_lines_image_id_reading_order", "image_id", "reading_order"),)

    id: int | None = Field(default=None, primary_key=True)
    image_id: int = Field(foreign_key="images.id")
    reading_order: int
    rec_text: str
    corrected_text: str | None = None
    score: float
    polygon: list[list[float]] = Field(sa_column=Column(JSON, nullable=False))
    status: LineStatus = Field(default=LineStatus.unreviewed)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def final_text(self) -> str:
        return self.rec_text if self.corrected_text is None else self.corrected_text
