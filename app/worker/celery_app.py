from celery import Celery
from celery.signals import worker_process_init

from app.config import get_settings
from app.db import configure_engine

settings = get_settings()

celery = Celery(
    "arabic_ocr",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.worker.tasks"],
)
celery.conf.update(
    task_acks_late=True,  # a crashed worker re-queues its image
    # acks_late alone isn't enough: if a forked worker process is SIGKILLed
    # mid-task (e.g. OOM-killed), Celery's default is to ack the message as
    # failed rather than redeliver it - the DB row is left stuck at
    # "running" forever, since run_ocr_for_image's own except block never
    # gets a chance to run when the process is just gone. This makes a
    # worker-lost task get rejected (requeued) instead, so another worker
    # picks the same image back up automatically.
    #
    # Known tradeoff, not mitigated here: if a specific image reliably
    # OOMs whichever worker processes it, this will crash-loop rather than
    # eventually giving up and marking it failed - there is no retry-count
    # cap on this mechanism (it isn't the same as task.retry()/max_retries,
    # which only applies to exceptions raised inside a task that keeps
    # running). Worth adding a cap if that turns out to matter in practice.
    task_reject_on_worker_lost=True,
    # Celery's default here is 4 seconds - nowhere near enough for
    # worker_process_init below to load three PaddleOCR models. Confirmed by
    # actually triggering a worker replacement (killed a fork mid-task): the
    # 4s default made celery's own pool supervisor kill every replacement
    # fork for "Timed out waiting for UP message" before it finished loading
    # models, respawn another, kill that one too - a permanent crash-loop
    # from a single lost worker, which is worse than the bug
    # task_reject_on_worker_lost exists to fix. Not a tuning nicety, a
    # required pairing with it.
    worker_proc_alive_timeout=120,
    worker_prefetch_multiplier=1,  # no hoarding; long tasks spread evenly
    task_track_started=True,
    broker_connection_retry_on_startup=True,
)


@worker_process_init.connect
def _init_worker_process(**_kwargs) -> None:
    """Build the DB engine and load PaddleOCR once per forked child.

    Prefork forks before this runs, so the model is built inside the child.
    Building it at import time would build it in the parent and fork a live
    paddle runtime, which is not reliably fork-safe.
    """
    from app.dispatch import get_engine

    configure_engine()
    get_engine().warmup()
