from pathlib import Path

import pytest
from PIL import Image as PILImage
from sqlmodel import select

from app.models import Batch, Image, ImageStatus, Line
from app.ocr.fake_engine import FakeOcrEngine


@pytest.fixture
def image_row(session, tmp_path) -> Image:
    path = tmp_path / "a.png"
    PILImage.new("RGB", (1000, 400), "white").save(path)
    batch = Batch(name="b", source_dir=str(tmp_path))
    session.add(batch)
    session.commit()
    image = Image(
        batch_id=batch.id,
        path=str(path),
        filename="a.png",
        sha256="h",
        width=1000,
        height=400,
        status=ImageStatus.queued,
    )
    session.add(image)
    session.commit()
    return image


def test_task_body_writes_lines_and_marks_done(session, image_row):
    from app.worker import tasks

    tasks.run_ocr(session, image_row.id, FakeOcrEngine())
    session.refresh(image_row)

    assert image_row.status is ImageStatus.done
    assert image_row.ocr_ms is not None
    lines = session.exec(select(Line).where(Line.image_id == image_row.id)).all()
    assert len(lines) == 4


def test_task_body_records_failure(session, image_row):
    from app.worker import tasks

    class Boom:
        def warmup(self) -> None:
            """No model to build."""

        def run(self, path: Path):
            raise RuntimeError("model exploded")

    tasks.run_ocr(session, image_row.id, Boom())
    session.refresh(image_row)

    assert image_row.status is ImageStatus.failed
    assert "model exploded" in image_row.error


def test_rerunning_replaces_lines_instead_of_appending(session, image_row):
    from app.worker import tasks

    tasks.run_ocr(session, image_row.id, FakeOcrEngine())
    tasks.run_ocr(session, image_row.id, FakeOcrEngine())
    lines = session.exec(select(Line).where(Line.image_id == image_row.id)).all()
    assert len(lines) == 4
