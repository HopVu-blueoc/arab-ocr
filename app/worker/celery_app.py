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
