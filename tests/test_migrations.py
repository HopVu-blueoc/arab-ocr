"""Migrations are the schema's source of truth in production; the test suite
builds it with create_all. These tests are what stops the two from drifting.
"""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlmodel import SQLModel, create_engine

from app.migrate import alembic_config


def test_single_head():
    """Two heads means someone branched the chain and `upgrade head` is ambiguous."""
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    assert len(heads) == 1, f"expected one migration head, found {heads}"


def test_migrations_match_the_models(tmp_path, monkeypatch):
    # A file DB, not sqlite:// - in-memory vanishes between connections, so
    # Alembic would migrate one database and the comparison would inspect
    # another, empty one.
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app.config import get_settings

    get_settings.cache_clear()

    db_path = tmp_path / "migrated.db"
    url = f"sqlite:///{db_path}"

    config = alembic_config()
    config.set_main_option("sqlalchemy.url", url)
    engine = create_engine(url)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, SQLModel.metadata)

    get_settings.cache_clear()
    assert diff == [], (
        "migrations and app/models.py disagree - run "
        "`alembic revision --autogenerate` and commit the result:\n"
        f"{diff}"
    )


def test_downgrade_round_trips(tmp_path, monkeypatch):
    """A migration that cannot be undone cannot be rolled back in an incident."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app.config import get_settings

    get_settings.cache_clear()

    url = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", url)
    engine = create_engine(url)

    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        command.upgrade(config, "head")

    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, SQLModel.metadata) == []

    get_settings.cache_clear()
