"""Bring the database schema up to date. Entry point for the `migrate` service.

Lives in app/ rather than scripts/ because it is operational code the image
needs - .dockerignore excludes scripts/.

Handles the one case a plain `alembic upgrade head` gets wrong: a database
created before Alembic existed here already has the tables, so the baseline
revision would fail on CREATE TABLE. Those get stamped instead.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.db import configure_engine

BASELINE = "0001_baseline"


def alembic_config() -> Config:
    return Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))


def run_migrations() -> None:
    engine = configure_engine()
    tables = set(inspect(engine).get_table_names())
    config = alembic_config()

    if "alembic_version" not in tables and "batches" in tables:
        print(f"pre-Alembic database detected, stamping {BASELINE}")
        command.stamp(config, BASELINE)

    command.upgrade(config, "head")
    print("schema is up to date")


if __name__ == "__main__":
    run_migrations()
