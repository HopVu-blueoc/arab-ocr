from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_engine: Engine | None = None


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def configure_engine(url: str | None = None) -> Engine:
    global _engine
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.images_dir.mkdir(parents=True, exist_ok=True)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(
        url or f"sqlite:///{settings.db_path}",
        connect_args={"check_same_thread": False},
    )
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
