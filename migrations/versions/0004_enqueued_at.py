"""enqueued at

Revision ID: 0004_enqueued_at
Revises: 0003_ocr_generation
Create Date: 2026-09-15 13:05:29.839945
"""
from collections.abc import Sequence

# sqlmodel is imported because autogenerate renders SQLModel's string type as
# sqlmodel.sql.sqltypes.AutoString, which is unimportable without it.
import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = '0004_enqueued_at'
down_revision: str | None = '0003_ocr_generation'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable, no backfill: every pre-existing row reads as NULL, which is
    # indistinguishable from "never published" to app/reconcile.py. Any
    # image that was already sitting `queued` longer than
    # RECONCILE_ENQUEUE_GRACE_SECONDS gets one redundant republish on the
    # next sweep after this deploys - harmless (the atomic claim in
    # app/service.py absorbs the duplicate), just a one-time burst, not a
    # standing cost.
    with op.batch_alter_table('images', schema=None) as batch_op:
        batch_op.add_column(sa.Column('enqueued_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('images', schema=None) as batch_op:
        batch_op.drop_column('enqueued_at')
