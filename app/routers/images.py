import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import select

from app.db import SessionDep
from app.models import Image, Line
from app.schemas import ImageDetailOut, LineOut

router = APIRouter(prefix="/api/images", tags=["images"])


def line_out(line: Line) -> LineOut:
    return LineOut(**line.model_dump(), final_text=line.final_text)


@router.get("/{image_id}", response_model=ImageDetailOut)
def get_image(image_id: int, session: SessionDep) -> ImageDetailOut:
    image = session.get(Image, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")
    lines = session.exec(
        select(Line).where(Line.image_id == image_id).order_by(Line.reading_order)
    ).all()
    return ImageDetailOut(**image.model_dump(), lines=[line_out(ln) for ln in lines])


@router.get("/{image_id}/file")
def get_image_file(image_id: int, session: SessionDep) -> FileResponse:
    image = session.get(Image, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")
    path = Path(image.path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail=f"source file is gone: {path}")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)
