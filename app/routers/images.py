import mimetypes
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from sqlmodel import select

from app.config import get_settings
from app.db import SessionDep
from app.dispatch import enqueue_image, get_engine
from app.models import Image, ImageStatus, Line, utcnow
from app.schemas import DetectBoxRequest, ImageDetailOut, ImageOut, ImageUpdate, LineOut
from app.service import detect_box_for_image
from app.storage import ObjectNotFound, get_storage

router = APIRouter(prefix="/api/images", tags=["images"])


def line_out(line: Line) -> LineOut:
    return LineOut(**line.model_dump(), final_text=line.final_text)


_CHUNK_SIZE = 1 << 16


def _iter_and_close(stream: BinaryIO) -> Iterator[bytes]:
    """Stream chunks from a storage object, guaranteeing it gets closed.

    `StreamingResponse` never closes the body it wraps unless given a
    `background` task, so without this the local backend leaks open file
    descriptors and the S3 backend leaks `StreamingBody` connections -
    on success, on a client disconnect, or on any exception mid-iteration.
    """
    try:
        while chunk := stream.read(_CHUNK_SIZE):
            yield chunk
    finally:
        stream.close()


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
        raise HTTPException(status_code=410, detail="stored image is gone") from None
    media_type = mimetypes.guess_type(Path(image.path).name)[0] or "application/octet-stream"
    return StreamingResponse(_iter_and_close(stream), media_type=media_type)


APPROVABLE = {ImageStatus.done, ImageStatus.approved}


@router.patch("/{image_id}", response_model=ImageOut)
def update_image(image_id: int, payload: ImageUpdate, session: SessionDep) -> Image:
    """The only reviewer-meaningful transition here is approving.

    Every other status is either a job-lifecycle state `claim_for_ocr` owns
    exclusively, or a place a client could otherwise use this endpoint to walk
    a `done` image back to `failed` and then hit `/retry` - which would bypass
    the generation guard that protects reviewer corrections entirely, since
    `retry` trusts `status == failed` as its only precondition.
    """
    if payload.status is not ImageStatus.approved:
        raise HTTPException(
            status_code=422, detail="status can only be set to 'approved' here"
        )

    image = session.get(Image, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")
    if image.status not in APPROVABLE:
        raise HTTPException(
            status_code=409, detail=f"cannot approve an image in state {image.status}"
        )

    image.status = payload.status
    image.updated_at = utcnow()
    session.add(image)
    session.commit()
    session.refresh(image)
    return image


@router.post("/{image_id}/retry", response_model=ImageOut)
def retry_image(image_id: int, session: SessionDep) -> Image:
    """Re-run OCR on a failed image.

    Only from `failed`: a `done`/`approved` image may carry reviewer
    corrections, and `run_ocr_for_image` still deletes and recreates every
    Line row on a run it actually claims.

    Bumping ocr_generation is what makes this a deliberate new run rather than
    a stale message reviving: the worker only claims a task whose generation
    matches the row's current one (see app/service.py:claim_for_ocr), so any
    task still in flight for the old generation - including one this same
    request superseded - can never re-run against this row.
    """
    image = session.get(Image, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")
    if image.status != ImageStatus.failed:
        raise HTTPException(
            status_code=409, detail=f"can only retry a failed image, not {image.status}"
        )

    image.status = ImageStatus.queued
    image.error = None
    image.ocr_generation += 1
    image.enqueued_at = None  # this generation has no publish yet
    image.updated_at = utcnow()
    session.add(image)
    session.commit()
    session.refresh(image)

    enqueue_image(image.id, image.ocr_generation)
    session.refresh(image)  # the inline job backend advances status synchronously
    return image


MIN_MANUAL_BOX_SIDE = 8  # image pixels; a stray click, not a drag


@router.post("/{image_id}/lines/detect-box")
def detect_box(image_id: int, payload: DetectBoxRequest, session: SessionDep):
    """Recognise one reviewer-drawn box.

    Cheap validation (box size, image existence, engine capability) happens
    synchronously here - none of it touches OCR models. The actual
    recognition (app/service.py:detect_box_for_image) runs wherever
    JOB_BACKEND says: inline mode calls it directly and returns the updated
    lines at 200, same as before; celery mode dispatches it to the worker and
    returns 202 with a job id for GET /api/jobs/{job_id} to poll. Running
    inference here in the API process was the actual defect - this container
    has no GPU in the deployed topology (only `worker` does), and nothing
    serialises concurrent request threads sharing one engine singleton.
    """
    xs = [p[0] for p in payload.polygon]
    ys = [p[1] for p in payload.polygon]
    if max(xs) - min(xs) < MIN_MANUAL_BOX_SIDE or max(ys) - min(ys) < MIN_MANUAL_BOX_SIDE:
        raise HTTPException(status_code=400, detail="box is too small")

    if session.get(Image, image_id) is None:
        raise HTTPException(status_code=404, detail="image not found")

    engine = get_engine()
    if not hasattr(engine, "recognize_quad"):
        raise HTTPException(
            status_code=501,
            detail="manual box detection isn't supported with the paddle_vl engine",
        )

    if get_settings().job_backend == "celery":
        from app.worker.tasks import detect_box_task

        job = detect_box_task.delay(image_id, payload.polygon)
        return JSONResponse(status_code=202, content={"job_id": job.id})

    result = detect_box_for_image(session, image_id, payload.polygon, engine)
    if not result["ok"]:
        raise HTTPException(status_code=result["status_code"], detail=result["reason"])
    return result["lines"]
