"""add execution checkpoint to tasks

Revision ID: f6a41746c0de
Revises: d4f8b1c73a20
Create Date: 2026-08-13 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from lib.db.migration_helpers import preserve_sqlite_indexes

revision: str = "f6a41746c0de"
down_revision: str | Sequence[str] | None = "d4f8b1c73a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("execution_checkpoint_json", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with preserve_sqlite_indexes("tasks"):
        with op.batch_alter_table("tasks", schema=None) as batch_op:
            batch_op.drop_column("execution_checkpoint_json")
