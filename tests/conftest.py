import pytest
from PIL import Image as PILImage
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JOB_BACKEND", "inline")  # never touch Redis from tests
    from app.config import get_settings

    get_settings.cache_clear()

    from fastapi.testclient import TestClient

    from app import db, dispatch
    from app.main import create_app
    from app.ocr.fake_engine import FakeOcrEngine

    db.configure_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.init_db()
    monkeypatch.setattr(dispatch, "_engine", FakeOcrEngine())
    yield TestClient(create_app())
    get_settings.cache_clear()


@pytest.fixture
def source_dir(tmp_path):
    d = tmp_path / "incoming"
    d.mkdir()
    # Each file gets a distinct pixel so their sha256 differ; identical blanks
    # would be deduplicated on import and the batch would hold one image.
    for index, name in enumerate(("one.png", "two.png")):
        img = PILImage.new("RGB", (1000, 400), "white")
        img.putpixel((index, 0), (0, 0, 0))
        img.save(d / name)
    (d / "notes.txt").write_text("ignore me", encoding="utf-8")
    return d


@pytest.fixture
def session_factory():
    from sqlmodel import Session

    from app.db import get_engine

    def factory():
        return Session(get_engine())

    return factory
