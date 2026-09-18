"""GET /api/health used to always answer {"status": "ok"} regardless of
whether the database or storage backend actually worked. These check that
a real failure is visible, and that a broker/backlog issue is reported
without making the API refuse otherwise-healthy traffic.
"""

from app.config import Settings
from app.health import build_health_report
from app.models import Batch, Image, ImageStatus


def test_healthy_stack_reports_ok(client, session_factory):
    from app.config import get_settings
    from app.storage import get_storage

    with session_factory() as session:
        status_code, body = build_health_report(session, get_storage(), get_settings())

    assert status_code == 200
    assert body["status"] == "ok"
    assert body["checks"]["database"] == {"ok": True}
    assert body["checks"]["storage"] == {"ok": True}


def test_a_broken_database_session_is_a_503():
    from sqlmodel import Session, create_engine

    from app.storage import get_storage

    # A path whose parent directory does not exist: SQLite cannot open it,
    # a real analogue of the disk/permissions failures this check exists for.
    broken_engine = create_engine("sqlite:////nonexistent/dir/that/cannot/exist.db")
    with Session(broken_engine) as session:
        status_code, body = build_health_report(
            session, get_storage(), Settings(job_backend="inline")
        )

    assert status_code == 503
    assert body["status"] == "degraded"
    assert body["checks"]["database"]["ok"] is False


def test_a_broken_storage_backend_is_a_503(session_factory):
    with session_factory() as session:

        class BrokenStorage:
            def exists(self, key):
                raise ConnectionError("rustfs unreachable")

        status_code, body = build_health_report(
            session, BrokenStorage(), Settings(job_backend="inline")
        )

    assert status_code == 503
    assert body["checks"]["storage"]["ok"] is False


def test_broker_and_backlog_are_reported_but_never_fail_the_request(session_factory):
    """A Celery outage doesn't stop the API from serving reads, and
    app/reconcile.py exists to recover once it's back - the health check
    should say so, not go 503 over it."""
    from app.storage import get_storage

    with session_factory() as session:
        status_code, body = build_health_report(
            session, get_storage(), Settings(job_backend="celery", redis_url="redis://nope:1/0")
        )

    assert status_code == 200  # database + storage are fine; that's what gates health
    assert body["checks"]["broker"]["ok"] is False
    assert "backlog" in body["checks"]


def test_broker_is_not_checked_under_the_inline_backend(session_factory):
    from app.storage import get_storage

    with session_factory() as session:
        _, body = build_health_report(session, get_storage(), Settings(job_backend="inline"))

    assert "broker" not in body["checks"]


def test_backlog_counts_only_queued_and_running(session_factory):
    from app.storage import get_storage

    with session_factory() as session:
        batch = Batch(name="b", source_dir="x")
        session.add(batch)
        session.commit()
        session.refresh(batch)

        def image(status):
            return Image(
                batch_id=batch.id,
                path="p",
                filename="f",
                sha256=f"h{status}",
                width=1,
                height=1,
                status=status,
            )

        session.add(image(ImageStatus.queued))
        session.add(image(ImageStatus.running))
        session.add(image(ImageStatus.done))
        session.add(image(ImageStatus.approved))
        session.commit()

        _, body = build_health_report(session, get_storage(), Settings(job_backend="inline"))

    assert body["checks"]["backlog"]["queued_or_running"] == 2


def test_the_real_endpoint_answers_200_when_healthy(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["storage"]["ok"] is True
