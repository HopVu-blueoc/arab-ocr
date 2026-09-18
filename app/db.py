from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import Depends
from sqlalchemy import event
from sqlalchemy.engine import Engine, make_url
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_engine: Engine | None = None


def _sqlite_pragmas(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    # NORMAL under WAL can lose the last transactions on power loss but never
    # corrupts, and a lost OCR result is recomputable. Both the upload path and
    # the per-image worker commit are commit-heavy, so the saved fsyncs matter.
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def apply_sqlite_pragmas(engine: Engine) -> None:
    """Register the PRAGMA hook on one engine.

    Per-engine, not on the Engine class: a class-level listener fires for every
    engine built in the process, which would send SQLite PRAGMAs to whatever
    else is connected.
    """
    event.listen(engine, "connect", _sqlite_pragmas)


def configure_engine(url: str | None = None) -> Engine:
    global _engine
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.images_dir.mkdir(parents=True, exist_ok=True)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)

    resolved = make_url(url or f"sqlite:///{settings.db_path}")
    is_sqlite = resolved.get_backend_name() == "sqlite"
    in_memory = is_sqlite and resolved.database in (None, "", ":memory:")

    kwargs: dict = {}
    if is_sqlite:
        # A sqlite3 kwarg; any other driver raises on it.
        kwargs["connect_args"] = {"check_same_thread": False}
    if not in_memory:
        # In-memory SQLite gets SingletonThreadPool, which takes no overflow.
        # WAL serialises writers, so a pool much larger than this just turns
        # pool-wait into "database is locked".
        kwargs |= {"pool_size": 10, "max_overflow": 20}

    _engine = create_engine(resolved, **kwargs)
    if is_sqlite:
        apply_sqlite_pragmas(_engine)
    return _engine


def get_engine() -> Engine:
    return _engine or configure_engine()


def init_db() -> None:
    # Importing the models registers them on SQLModel.metadata; without this
    # create_all() silently creates nothing.
    import app.models  # noqa: F401

    SQLModel.metadata.create_all(get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(get_engine()) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """For the Celery worker: one commit per image."""
    with Session(get_engine()) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
