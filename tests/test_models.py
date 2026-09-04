import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.models import Batch, Image, ImageStatus, Line, LineStatus


def test_final_text_prefers_correction(session):
    batch = Batch(name="b1", source_dir="/tmp/in")
    session.add(batch)
    session.commit()

    image = Image(
        batch_id=batch.id,
        path="/tmp/in/a.png",
        filename="a.png",
        sha256="abc",
        width=1000,
        height=400,
        status=ImageStatus.done,
    )
    session.add(image)
    session.commit()

    raw = Line(
        image_id=image.id,
        reading_order=0,
        rec_text="مرحبا",
        score=0.9,
        polygon=[[0, 0], [10, 0], [10, 5], [0, 5]],
    )
    fixed = Line(
        image_id=image.id,
        reading_order=1,
        rec_text="بالعالم",
        corrected_text="بالعالمين",
        score=0.7,
        polygon=[[0, 6], [10, 6], [10, 11], [0, 11]],
        status=LineStatus.edited,
    )
    session.add_all([raw, fixed])
    session.commit()

    lines = session.exec(select(Line).order_by(Line.reading_order)).all()
    assert [ln.final_text for ln in lines] == ["مرحبا", "بالعالمين"]
    assert lines[0].rec_text == "مرحبا"  # rec_text stays immutable
    assert lines[0].status is LineStatus.unreviewed
    assert lines[1].polygon == [[0, 6], [10, 6], [10, 11], [0, 11]]


def test_sha256_is_unique(session):
    batch = Batch(name="b", source_dir="/tmp")
    session.add(batch)
    session.commit()
    common = {"batch_id": batch.id, "width": 1, "height": 1}
    session.add(Image(path="/a.png", filename="a.png", sha256="dup", **common))
    session.commit()
    session.add(Image(path="/b.png", filename="b.png", sha256="dup", **common))
    with pytest.raises(IntegrityError):
        session.commit()
