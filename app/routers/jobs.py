"""Status/result polling for work dispatched to a Celery task.

Only detect_box uses this today (app/routers/images.py:detect_box). Celery's
own result backend (the same Redis instance as the broker) is the store -
no new table, no new persistence concern.
"""

from celery.result import AsyncResult
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.worker.celery_app import celery

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}")
def get_job(job_id: str) -> JSONResponse:
    result = AsyncResult(job_id, app=celery)

    if not result.ready():
        return JSONResponse(status_code=202, content={"status": "pending"})

    if result.failed():
        # An exception escaped the task body - a bug, not one of
        # detect_box_for_image's expected {"ok": False, ...} outcomes.
        raise HTTPException(status_code=500, detail=f"job failed: {result.result}")

    payload = result.result
    if not payload.get("ok", True):
        raise HTTPException(
            status_code=payload.get("status_code", 500), detail=payload.get("reason")
        )
    return JSONResponse(content=payload.get("lines", payload))
