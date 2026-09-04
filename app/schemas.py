from datetime import datetime

from pydantic import BaseModel

from app.models import ImageStatus, LineStatus


class BatchCreate(BaseModel):
    name: str
    source_dir: str


class BatchOut(BaseModel):
    id: int
    name: str
    source_dir: str
    created_at: datetime
    image_count: int = 0
    # Only meaningful on the create response: how many files this import
    # skipped as sha256 duplicates. Not persisted, so GET always reports 0.
    skipped_count: int = 0
    done_count: int = 0
    approved_count: int = 0
    failed_count: int = 0


class LineOut(BaseModel):
    id: int
    reading_order: int
    rec_text: str
    corrected_text: str | None
    final_text: str
    score: float
    polygon: list[list[float]]
    status: LineStatus


class ImageOut(BaseModel):
    id: int
    batch_id: int
    filename: str
    width: int
    height: int
    status: ImageStatus
    error: str | None


class ImageDetailOut(ImageOut):
    lines: list[LineOut]


class LineUpdate(BaseModel):
    corrected_text: str | None = None
    status: LineStatus | None = None


class ImageUpdate(BaseModel):
    status: ImageStatus
