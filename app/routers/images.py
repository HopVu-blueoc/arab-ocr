import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import select

from app.db import SessionDep
from app.models import Image, ImageStatus, Line, utcnow
from app.schemas import ImageDetailOut, ImageOut, ImageUpdate, LineOut
from app.storage import ObjectNotFound, get_storage

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
def get_image_file(image_id: int, session: SessionDep) -> StreamingResponse:
    image = session.get(Image, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")
    try:
        stream = get_storage().open(image.path)
    except ObjectNotFound:
        # 410 rather than 500: a vanished object is the reviewer's only signal
        # that the stored image is gone, and it is not a server fault.
        raise HTTPException(status_code=410, detail=f"stored image is gone: {image.path}") from None
    media_type = mimetypes.guess_type(Path(image.path).name)[0] or "application/octet-stream"
    return StreamingResponse(stream, media_type=media_type)


APPROVABLE = {ImageStatus.done, ImageStatus.approved}


@router.patch("/{image_id}", response_model=ImageOut)
def update_image(image_id: int, payload: ImageUpdate, session: SessionDep) -> Image:
    image = session.get(Image, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")
    if payload.status is ImageStatus.approved and image.status not in APPROVABLE:
        raise HTTPException(
            status_code=409, detail=f"cannot approve an image in state {image.status}"
        )

    image.status = payload.status
    image.updated_at = utcnow()
    session.add(image)
    session.commit()
    session.refresh(image)
    return image
