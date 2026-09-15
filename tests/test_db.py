from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from app.db import _sqlite_pragmas, get_engine


def _pragma(engine, name: str):
    with engine.connect() as conn:
        return conn.execute(text(f"PRAGMA {name}")).scalar()


def test_sqlite_pragmas_are_applied(client):
    engine = get_engine()
    assert _pragma(engine, "journal_mode") == "wal"
    assert _pragma(engine, "synchronous") == 1  # NORMAL
    assert _pragma(engine, "busy_timeout") == 10000
    assert _pragma(engine, "foreign_keys") == 1


def test_pragma_hook_is_not_registered_on_the_engine_class(client):
    """A class-level listener would fire for every engine in the process."""
    assert event.contains(Engine, "connect", _sqlite_pragmas) is False
    assert event.contains(get_engine(), "connect", _sqlite_pragmas) is True


def test_foreign_keys_enforced_in_engine_fixture(engine):
    from sqlmodel import Session

    from app.models import Line

    with Session(engine) as session:
        session.add(Line(image_id=999, reading_order=0, rec_text="x", score=1.0, polygon=[]))
        try:
            session.commit()
        except Exception:
            return
    raise AssertionError("expected a foreign key violation for a dangling image_id")
