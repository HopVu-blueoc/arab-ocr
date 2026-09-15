from logging.config import fileConfig

from alembic import context
from sqlmodel import SQLModel

# Importing the models is what populates SQLModel.metadata; without it
# autogenerate sees an empty schema and happily writes a migration that drops
# every table. Same trap app/db.py:init_db documents.
import app.models  # noqa: F401
from app.db import configure_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # SQLite cannot ALTER TABLE DROP/ALTER COLUMN, so anything beyond an
        # add-column needs op.batch_alter_table's copy-and-swap.
        render_as_batch=connection.dialect.name == "sqlite",
    )


def run_migrations_offline() -> None:
    context.configure(
        url=str(configure_engine().url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # A caller (the drift test) can hand us a live connection to run against.
    connectable = config.attributes.get("connection", None)
    if connectable is not None:
        _configure(connectable)
        with context.begin_transaction():
            context.run_migrations()
        return

    with configure_engine().connect() as connection:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
