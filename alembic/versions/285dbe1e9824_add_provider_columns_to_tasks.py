"""add provider columns to tasks

Revision ID: 285dbe1e9824
Revises: 8b1e8a1290ca
Create Date: 2026-05-25 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from lib.db.migration_helpers import preserve_sqlite_indexes

# revision identifiers, used by Alembic.
revision: str = "285dbe1e9824"
down_revision: str | Sequence[str] | None = "8b1e8a1290ca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("provider_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("provider_job_id", sa.String(), nullable=True))
        batch_op.create_index(
            "idx_tasks_status_provider_queued",
            ["status", "provider_id", "queued_at"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema.

    本迁移自己建的索引在重建表之外先删——``preserve_sqlite_indexes`` 分不清有意删除与静默丢失，
    放在它里面会被当作丢失回放，而 provider_id 此时已不在表上，回放必然报错。
    """
    op.drop_index("idx_tasks_status_provider_queued", table_name="tasks")
    with preserve_sqlite_indexes("tasks"):
        with op.batch_alter_table("tasks", schema=None) as batch_op:
            batch_op.drop_column("provider_job_id")
            batch_op.drop_column("provider_id")
