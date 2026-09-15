"""Recovering OCR jobs the normal flow lost track of.

Two distinct failure modes, both invisible to the reviewer unless this runs:

* A request stored an image and committed it as `queued`, but the Celery
  publish that was supposed to follow never happened - Redis was down, or
  the API process died between the two (dispatch.py's own retry already
  covers a brief blip; this is the backstop for everything past that). No
  message exists anywhere for this row, so nothing will ever redeliver it -
  unlike a crashed worker, there is no in-flight message to lose.
* A worker died in a way Celery's own task_reject_on_worker_lost redelivery
  didn't recover from - e.g. the broker itself lost the unacked message
  before the crash was detected. The row is stuck at `running` with no
  worker ever coming back for it.

Both are handled by re-dispatching through the same app.dispatch.enqueue_image
that a normal upload uses, so the same atomic claim in app/service.py governs
what happens if the "lost" job was not actually lost - the worst case is one
redundant, harmless message.
"""

import logging
from datetime import timedelta

from sqlmodel import Session, select

from app.dispatch import enqueue_image
from app.models import Image, ImageStatus, utcnow

logger = logging.getLogger(__name__)


def find_lost_enqueues(session: Session, grace_seconds: int) -> list[Image]:
    """Queued rows whose publish never even happened.

    enqueued_at is the differentiator: a row waiting in a deep, legitimate
    backlog has it set from the moment its message actually went out,
    however long the job then waits its turn. Only a row where the publish
    itself never completed stays NULL indefinitely.
    """
    cutoff = utcnow() - timedelta(seconds=grace_seconds)
    return session.exec(
        select(Image)
        .where(Image.status == ImageStatus.queued)
        .where(Image.enqueued_at.is_(None))
        .where(Image.updated_at < cutoff)
    ).all()


def find_stuck_running(session: Session, timeout_seconds: int) -> list[Image]:
    """Rows Celery's own crash recovery did not bring back.

    A generous, time-based heuristic, not a precise liveness check - there is
    no heartbeat, so "stuck" here means "running far longer than any real
    image should take", tunable via RECONCILE_RUNNING_TIMEOUT_SECONDS.
    """
    cutoff = utcnow() - timedelta(seconds=timeout_seconds)
    return session.exec(
        select(Image)
        .where(Image.status == ImageStatus.running)
        .where(Image.updated_at < cutoff)
    ).all()


def reconcile(
    session: Session,
    *,
    enqueue_grace_seconds: int,
    running_timeout_seconds: int,
    enqueue=enqueue_image,
) -> dict[str, int]:
    """Run one sweep. Returns counts for logging/observability only."""
    republished = 0
    for image in find_lost_enqueues(session, enqueue_grace_seconds):
        logger.warning(
            "image %s queued since %s with no successful publish - republishing",
            image.id,
            image.updated_at,
        )
        enqueue(image.id, image.ocr_generation)
        republished += 1

    reassigned = 0
    for image in find_stuck_running(session, running_timeout_seconds):
        logger.warning(
            "image %s stuck running since %s - reassigning as a new generation",
            image.id,
            image.updated_at,
        )
        # A new generation, not a republish of the same one: unlike Celery's
        # own crash detection, this heuristic has no certainty the original
        # attempt is actually dead, so it does not reuse the same message
        # identity claim_for_ocr would otherwise treat as a safe resume.
        image.status = ImageStatus.queued
        image.ocr_generation += 1
        image.enqueued_at = None
        image.updated_at = utcnow()
        session.add(image)
        session.commit()
        enqueue(image.id, image.ocr_generation)
        reassigned += 1

    return {"republished": republished, "reassigned": reassigned}
