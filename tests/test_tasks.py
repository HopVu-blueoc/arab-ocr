from pathlib import Path

import pytest
from PIL import Image as PILImage
from sqlmodel import select

from app.models import Batch, Image, ImageStatus, Line
from app.ocr.fake_engine import FakeOcrEngine


@pytest.fixture
def image_row(session, tmp_path, monkeypatch) -> Image:
    """path is a storage key now: put the file through a real LocalStorage
    rooted at tmp_path and inject it as the get_storage() singleton, the same
    way run_ocr_for_image will look it up."""
    from app import storage as storage_module
    from app.storage.local import LocalStorage

    storage = LocalStorage(tmp_path / "objects")
    monkeypatch.setattr(storage_module, "_storage", storage)

    path = tmp_path / "a.png"
    PILImage.new("RGB", (1000, 400), "white").save(path)
    key = "batch-1/a.png"
    storage.put(key, path)

    batch = Batch(name="b", source_dir=str(tmp_path))
    session.add(batch)
    session.commit()
    image = Image(
        batch_id=batch.id,
        path=key,
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

    tasks.run_ocr(session, image_row.id, FakeOcrEngine(), image_row.ocr_generation)
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

    tasks.run_ocr(session, image_row.id, Boom(), image_row.ocr_generation)
    session.refresh(image_row)

    assert image_row.status is ImageStatus.failed
    assert "model exploded" in image_row.error


def test_a_redelivered_task_for_a_finished_image_is_a_noop(session, image_row):
    """The scenario P1 #4 names: acks_late + reject_on_worker_lost can
    redeliver a task after the original already committed. The redelivery
    carries the same generation and finds the image no longer queued, so it
    must not touch the row at all - not the status, not the lines, and it
    must not even invoke the engine."""
    from app.worker import tasks

    generation = image_row.ocr_generation
    engine = FakeOcrEngine()

    tasks.run_ocr(session, image_row.id, engine, generation)
    session.refresh(image_row)
    assert image_row.status is ImageStatus.done
    first_updated_at = image_row.updated_at

    tasks.run_ocr(session, image_row.id, engine, generation)  # the redelivery
    session.refresh(image_row)

    assert len(engine.calls) == 1, "the engine ran a second time for a stale redelivery"
    assert image_row.status is ImageStatus.done
    assert image_row.updated_at == first_updated_at
    lines = session.exec(select(Line).where(Line.image_id == image_row.id)).all()
    assert len(lines) == 4


def test_an_intentional_retry_with_a_new_generation_replaces_lines(session, image_row):
    """The counterpart: a deliberate retry (a new generation, status reset to
    queued - what retry_image does) is a real rerun and does replace the
    line set, exactly as before this fix."""
    from app.worker import tasks

    tasks.run_ocr(session, image_row.id, FakeOcrEngine(), image_row.ocr_generation)
    session.refresh(image_row)

    image_row.status = ImageStatus.queued
    image_row.ocr_generation += 1
    session.add(image_row)
    session.commit()

    engine = FakeOcrEngine()
    tasks.run_ocr(session, image_row.id, engine, image_row.ocr_generation)
    session.refresh(image_row)

    assert len(engine.calls) == 1, "a genuine new generation was not claimed"
    assert image_row.status is ImageStatus.done
    lines = session.exec(select(Line).where(Line.image_id == image_row.id)).all()
    assert len(lines) == 4


def test_a_stale_generation_is_never_claimed_even_while_queued(session, image_row):
    """If the row somehow reads 'queued' again (a legitimate new generation
    was dispatched) but a stale message from an older generation arrives, the
    old message must not claim it - only the current generation may."""
    from app.worker import tasks

    image_row.ocr_generation = 5
    session.add(image_row)
    session.commit()

    engine = FakeOcrEngine()
    tasks.run_ocr(session, image_row.id, engine, 4)  # stale generation
    session.refresh(image_row)

    assert len(engine.calls) == 0
    assert image_row.status is ImageStatus.queued  # untouched, not claimed


def test_a_same_generation_redelivery_resumes_a_row_stuck_running(session, image_row):
    """This is what celery_app.py's task_reject_on_worker_lost promises:
    a worker that was SIGKILLed mid-task leaves the row at 'running' with no
    one left to finish it, and Celery redelivers the same message so another
    worker picks it back up. The claim has to accept that redelivery from
    'running', or the promise is broken - the row would stay stuck forever."""
    from app.service import claim_for_ocr

    image_row.status = ImageStatus.running  # as if a worker claimed it and died
    session.add(image_row)
    session.commit()

    claimed = claim_for_ocr(session, image_row.id, image_row.ocr_generation)

    assert claimed is not None
    assert claimed.status is ImageStatus.running  # re-claimed, not left alone


def test_a_different_generation_still_cannot_claim_a_running_row(session, image_row):
    """The running-reclaim path is scoped to the SAME generation only - a
    stale or superseded generation must not be able to grab a row that is
    legitimately running under a newer one."""
    from app.service import claim_for_ocr

    image_row.status = ImageStatus.running
    image_row.ocr_generation = 5
    session.add(image_row)
    session.commit()

    assert claim_for_ocr(session, image_row.id, 4) is None
