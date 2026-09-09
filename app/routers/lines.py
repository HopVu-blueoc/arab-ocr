from fastapi import APIRouter, HTTPException
from sqlmodel import select

from app.db import SessionDep
from app.models import Line, LineStatus, utcnow
from app.ocr.reading_order import reorder_lines
from app.routers.images import line_out
from app.schemas import LineOut, LineUpdate

router = APIRouter(prefix="/api/lines", tags=["lines"])


@router.patch("/{line_id}", response_model=LineOut)
def update_line(line_id: int, payload: LineUpdate, session: SessionDep) -> LineOut:
    line = session.get(Line, line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="line not found")

    fields = payload.model_dump(exclude_unset=True)
    if "corrected_text" in fields:
        text = fields["corrected_text"]
        line.corrected_text = text
        # An explicit status in the same request still wins.
        line.status = LineStatus.unreviewed if text is None else LineStatus.edited
    if fields.get("status") is not None:
        line.status = fields["status"]

    line.updated_at = utcnow()
    session.add(line)
    session.commit()
    session.refresh(line)
    return line_out(line)


@router.delete("/{line_id}", response_model=list[LineOut])
def delete_line(line_id: int, session: SessionDep) -> list[LineOut]:
    line = session.get(Line, line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="line not found")

    image_id = line.image_id
    session.delete(line)
    session.commit()

    remaining = session.exec(select(Line).where(Line.image_id == image_id)).all()
    for index, ln in enumerate(reorder_lines(remaining)):
        ln.reading_order = index
        session.add(ln)
    session.commit()

    ordered = session.exec(
        select(Line).where(Line.image_id == image_id).order_by(Line.reading_order)
    ).all()
    return [line_out(ln) for ln in ordered]
