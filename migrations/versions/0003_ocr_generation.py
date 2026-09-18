"""ocr generation

Revision ID: 0003_ocr_generation
Revises: 0002_perf_indexes
Create Date: 2026-09-15 12:55:42.851482
"""
from collections.abc import Sequence

# sqlmodel is imported because autogenerate renders SQLModel's string type as
# sqlmodel.sql.sqltypes.AutoString, which is unimportable without it.
import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = '0003_ocr_generation'
down_revision: str | None = '0002_perf_indexes'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default backfills every existing row to 1, the same value new
    # rows get from the model's Field(default=1) - a NOT NULL add with no
    # default would fail outright against a database that already has images.
    with op.batch_alter_table('images', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('ocr_generation', sa.Integer(), nullable=False, server_default='1')
        )


def downgrade() -> None:
    with op.batch_alter_table('images', schema=None) as batch_op:
        batch_op.drop_column('ocr_generation')
