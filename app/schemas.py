from datetime import datetime

from pydantic import BaseModel

from app.models import ImageStatus, LineStatus


class BatchCreate(BaseModel):
    name: str


class BatchOut(BaseModel):
    id: int
    name: str
    # The storage prefix this batch's objects live under. Informational for
    # operators; never a client-supplied value.
    source_dir: str
    created_at: datetime
    image_count: int = 0
    done_count: int = 0
    approved_count: int = 0
    failed_count: int = 0


class UploadFailure(BaseModel):
    filename: str
    reason: str


class UploadResult(BaseModel):
    imported: int
    skipped: int  # bytes already imported, possibly into another batch
    failed: list[UploadFailure] = []


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
