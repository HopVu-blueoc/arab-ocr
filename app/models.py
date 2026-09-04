from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Column, UniqueConstraint
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

    id: int | None = Field(default=None, primary_key=True)
    name: str
    source_dir: str
    created_at: datetime = Field(default_factory=utcnow)


class Image(SQLModel, table=True):
    __tablename__ = "images"
    __table_args__ = (UniqueConstraint("sha256", name="uq_images_sha256"),)

    id: int | None = Field(default=None, primary_key=True)
    batch_id: int = Field(foreign_key="batches.id", index=True)
    path: str
    filename: str
    sha256: str = Field(index=True)
    width: int
    height: int
    status: ImageStatus = Field(default=ImageStatus.pending, index=True)
    error: str | None = None
    ocr_ms: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Line(SQLModel, table=True):
    __tablename__ = "lines"

    id: int | None = Field(default=None, primary_key=True)
    image_id: int = Field(foreign_key="images.id", index=True)
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
