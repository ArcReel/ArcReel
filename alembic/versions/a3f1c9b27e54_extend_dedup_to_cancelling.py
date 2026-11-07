"""extend dedup index to include cancelling status

Revision ID: a3f1c9b27e54
Revises: 285dbe1e9824
Create Date: 2026-05-25 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f1c9b27e54"
down_revision: str | Sequence[str] | None = "285dbe1e9824"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_dedup_index_if_exists() -> None:
    """Cross-dialect safe drop: historical migration drift may mean the index never existed; avoid OperationalError."""
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect in ("sqlite", "postgresql"):
        op.execute("DROP INDEX IF EXISTS idx_tasks_dedupe_active")
    else:
        op.drop_index("idx_tasks_dedupe_active", table_name="tasks")


def upgrade() -> None:
    """Recreate idx_tasks_dedupe_active with 'cancelling' in the partial WHERE.

    Include cancelling to prevent immediate redo after cancelling running tasks from creating concurrency.
    While a worker still handles CancelledError, partial uniqueness must deduplicate same-resource
    enqueues to the cancelling task_id; otherwise two tasks write the same output file concurrently.
    """
    _drop_dedup_index_if_exists()
    op.create_index(
        "idx_tasks_dedupe_active",
        "tasks",
        ["project_name", "task_type", "resource_id", sa.text("COALESCE(script_file, '')")],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'cancelling')"),
        sqlite_where=sa.text("status IN ('queued', 'running', 'cancelling')"),
    )


def downgrade() -> None:
    """Restore original WHERE clause without 'cancelling'."""
    _drop_dedup_index_if_exists()
    op.create_index(
        "idx_tasks_dedupe_active",
        "tasks",
        ["project_name", "task_type", "resource_id", sa.text("COALESCE(script_file, '')")],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
        sqlite_where=sa.text("status IN ('queued', 'running')"),
    )
