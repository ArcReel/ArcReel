"""add execution progress to tasks

Revision ID: d82b14f6c921
Revises: 9d96871835a1
Create Date: 2026-08-23 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from lib.db.migration_helpers import preserve_sqlite_indexes

revision: str = "d82b14f6c921"
down_revision: str | Sequence[str] | None = "9d96871835a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("execution_progress_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with preserve_sqlite_indexes("tasks"):
        with op.batch_alter_table("tasks", schema=None) as batch_op:
            batch_op.drop_column("execution_progress_json")
