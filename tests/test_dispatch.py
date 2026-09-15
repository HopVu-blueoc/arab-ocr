"""dispatch.enqueue_image's Celery path: retry a transient publish failure,
and never let a persistent one raise into the caller.
"""

from app.config import get_settings
from app.models import Batch, Image, ImageStatus


def _queued_image(session_factory, generation: int = 1) -> int:
    with session_factory() as session:
        batch = Batch(name="b", source_dir="x")
        session.add(batch)
        session.commit()
        session.refresh(batch)
        image = Image(
            batch_id=batch.id,
            path="p",
            filename="f.png",
            sha256=f"h{id(session)}",
            width=1,
            height=1,
            status=ImageStatus.queued,
            ocr_generation=generation,
        )
        session.add(image)
        session.commit()
        session.refresh(image)
        return image.id


def test_a_transient_publish_failure_is_retried_and_then_marks_enqueued(
    client, session_factory, monkeypatch
):
    from app import dispatch
    from app.worker import tasks

    monkeypatch.setattr(get_settings(), "job_backend", "celery")
    monkeypatch.setattr(dispatch, "_PUBLISH_RETRY_BASE_SECONDS", 0)  # no real sleep in tests

    calls = {"n": 0}

    def flaky_delay(image_id, generation):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("redis unreachable")

    monkeypatch.setattr(tasks.ocr_image, "delay", flaky_delay)

    image_id = _queued_image(session_factory)
    dispatch.enqueue_image(image_id, 1)

    assert calls["n"] == 3, "should have retried the first two failures"
    with session_factory() as session:
        assert session.get(Image, image_id).enqueued_at is not None


def test_a_persistent_publish_failure_never_raises_and_never_marks_enqueued(
    client, session_factory, monkeypatch
):
    """One file's publish never succeeding must not fail the HTTP request for
    every other file in the same upload. The row stays exactly as it was -
    queued, unmarked - for app/reconcile.py to pick up later."""
    from app import dispatch
    from app.worker import tasks

    monkeypatch.setattr(get_settings(), "job_backend", "celery")
    monkeypatch.setattr(dispatch, "_PUBLISH_RETRY_BASE_SECONDS", 0)

    def always_fails(image_id, generation):
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(tasks.ocr_image, "delay", always_fails)

    image_id = _queued_image(session_factory)
    dispatch.enqueue_image(image_id, 1)  # must not raise

    with session_factory() as session:
        row = session.get(Image, image_id)
        assert row.status is ImageStatus.queued
        assert row.enqueued_at is None
