"""What GET /api/health actually checks.

Kept out of app/main.py and pure enough to test without an HTTP client: each
check takes the resource it examines (a session, a storage backend, a
broker URL) and returns a plain dict, never raising.
"""

from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from app.config import Settings
from app.models import Image, ImageStatus
from app.storage.base import Storage

BACKLOG_STATUSES = (ImageStatus.queued, ImageStatus.running)


def check_database(session: Session) -> dict[str, Any]:
    try:
        session.exec(select(1)).one()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 - reported, not re-raised
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def check_storage(storage: Storage) -> dict[str, Any]:
    try:
        # A key that will never exist; the point is exercising real
        # connectivity (S3: an actual round trip to RustFS; local: a real
        # filesystem stat), not the answer.
        storage.exists("__healthcheck__")
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 - reported, not re-raised
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def check_broker(redis_url: str) -> dict[str, Any]:
    try:
        from redis import Redis

        Redis.from_url(redis_url, socket_connect_timeout=2).ping()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 - reported, not re-raised
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def backlog_count(session: Session) -> int:
    return session.exec(
        select(func.count())
        .select_from(Image)
        .where(Image.status.in_(BACKLOG_STATUSES))
    ).one()


def build_health_report(
    session: Session, storage: Storage, settings: Settings
) -> tuple[int, dict[str, Any]]:
    """Returns (http_status_code, response_body).

    database and storage are load-bearing for every request this API serves,
    so either one failing is a 503. The broker and the backlog count are
    reported for visibility, not treated as fatal: a Celery outage doesn't
    stop the API from answering reads, and app/reconcile.py exists precisely
    to recover once the broker comes back - the review's ask here was for
    the number to be *visible*, not for the API to refuse traffic over it.
    """
    checks: dict[str, Any] = {
        "database": check_database(session),
        "storage": check_storage(storage),
    }
    healthy = checks["database"]["ok"] and checks["storage"]["ok"]

    if settings.job_backend == "celery":
        checks["broker"] = check_broker(settings.redis_url)

    try:
        checks["backlog"] = {"queued_or_running": backlog_count(session)}
    except Exception as exc:  # noqa: BLE001 - a broken backlog count is informational
        checks["backlog"] = {"error": f"{type(exc).__name__}: {exc}"}

    status_code = 200 if healthy else 503
    return status_code, {"status": "ok" if healthy else "degraded", "checks": checks}
