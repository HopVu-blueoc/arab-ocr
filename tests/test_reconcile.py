"""The two recovery paths app/reconcile.py exists for.

The critical property under test isn't "a stuck job gets fixed" alone - it's
that a NORMAL backlog, where thousands of images sit `queued` for a long
time simply waiting their turn, is never mistaken for a lost job. Only
enqueued_at being unset distinguishes the two.
"""

from datetime import timedelta

from sqlmodel import Session

from app.models import Batch, Image, ImageStatus, utcnow
from app.reconcile import find_lost_enqueues, find_stuck_running, reconcile


def _image(session: Session, **overrides) -> Image:
    batch = session.exec(Batch.__table__.select()).first()
    if batch is None:
        batch = Batch(name="b", source_dir="batch-1")
        session.add(batch)
        session.commit()
        session.refresh(batch)
    defaults = {
        "batch_id": batch.id,
        "path": "k",
        "filename": "f.png",
        "sha256": f"h{overrides.get('id', id(overrides))}",
        "width": 1,
        "height": 1,
        "status": ImageStatus.queued,
    }
    defaults.update(overrides)
    image = Image(**defaults)
    session.add(image)
    session.commit()
    session.refresh(image)
    return image


def _age(session: Session, image: Image, seconds: int) -> None:
    image.updated_at = utcnow() - timedelta(seconds=seconds)
    session.add(image)
    session.commit()


def test_a_never_published_row_past_grace_is_found(session):
    image = _image(session, enqueued_at=None)
    _age(session, image, seconds=60)

    found = find_lost_enqueues(session, grace_seconds=30)

    assert [i.id for i in found] == [image.id]


def test_a_recently_stored_row_is_not_flagged_before_grace_elapses(session):
    """The normal window between commit and publish - must not be a false
    positive, or the sweep would race a legitimate publish that just
    hasn't happened yet."""
    _image(session, enqueued_at=None)  # updated_at is "now"

    assert find_lost_enqueues(session, grace_seconds=30) == []


def test_a_deep_but_legitimate_backlog_is_never_flagged(session):
    """The property this module exists to get right: a row published a long
    time ago that is simply still waiting its turn must not be treated as
    lost, no matter how long it has been queued."""
    image = _image(session, enqueued_at=utcnow())
    _age(session, image, seconds=100_000)  # queued for over a day, legitimately

    assert find_lost_enqueues(session, grace_seconds=30) == []


def test_a_recently_claimed_running_row_is_not_flagged(session):
    _image(session, status=ImageStatus.running, enqueued_at=utcnow())

    assert find_stuck_running(session, timeout_seconds=1800) == []


def test_a_running_row_past_the_timeout_is_found(session):
    image = _image(session, status=ImageStatus.running, enqueued_at=utcnow())
    _age(session, image, seconds=3600)

    found = find_stuck_running(session, timeout_seconds=1800)

    assert [i.id for i in found] == [image.id]


def test_a_done_image_is_never_flagged_by_either_check(session):
    image = _image(session, status=ImageStatus.done, enqueued_at=utcnow())
    _age(session, image, seconds=100_000)

    assert find_lost_enqueues(session, grace_seconds=30) == []
    assert find_stuck_running(session, timeout_seconds=1) == []


def test_reconcile_republishes_a_lost_enqueue_at_the_same_generation(session):
    image = _image(session, enqueued_at=None, ocr_generation=1)
    _age(session, image, seconds=60)

    calls = []
    reconcile(
        session,
        enqueue_grace_seconds=30,
        running_timeout_seconds=1800,
        enqueue=lambda image_id, generation: calls.append((image_id, generation)),
    )

    assert calls == [(image.id, 1)]
    session.refresh(image)
    assert image.status is ImageStatus.queued  # unchanged - same attempt, just republished


def test_reconcile_reassigns_a_stuck_running_job_to_a_new_generation(session):
    image = _image(session, status=ImageStatus.running, enqueued_at=utcnow(), ocr_generation=3)
    _age(session, image, seconds=3600)

    calls = []
    reconcile(
        session,
        enqueue_grace_seconds=30,
        running_timeout_seconds=1800,
        enqueue=lambda image_id, generation: calls.append((image_id, generation)),
    )

    assert calls == [(image.id, 4)]  # bumped, not replayed as generation 3
    session.refresh(image)
    assert image.status is ImageStatus.queued
    assert image.ocr_generation == 4
    assert image.enqueued_at is None


def test_reconcile_touches_nothing_when_there_is_nothing_stuck(session):
    image = _image(session, enqueued_at=utcnow())  # fresh, legitimately queued

    calls = []
    counts = reconcile(
        session,
        enqueue_grace_seconds=30,
        running_timeout_seconds=1800,
        enqueue=lambda *a: calls.append(a),
    )

    assert counts == {"republished": 0, "reassigned": 0}
    assert calls == []
    session.refresh(image)
    assert image.status is ImageStatus.queued
