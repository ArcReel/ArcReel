"""retire the cancelling task status

Revision ID: 23c60be146cc
Revises: c5a819b247de
Create Date: 2026-09-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "23c60be146cc"
down_revision: str | Sequence[str] | None = "c5a819b247de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_dedup_index() -> None:
    bind = op.get_bind()
    if bind.dialect.name in ("sqlite", "postgresql"):
        op.execute("DROP INDEX IF EXISTS idx_tasks_dedupe_active")
    else:
        op.drop_index("idx_tasks_dedupe_active", table_name="tasks")


def _create_dedup_index(active_statuses: str) -> None:
    where = sa.text(f"status IN ({active_statuses})")
    op.create_index(
        "idx_tasks_dedupe_active",
        "tasks",
        [
            "project_name",
            "user_id",
            "task_type",
            "resource_id",
            sa.text("COALESCE(script_file, '')"),
            sa.text("COALESCE(resource_type, '')"),
        ],
        unique=True,
        postgresql_where=where,
        sqlite_where=where,
    )


def upgrade() -> None:
    # Cascade queued descendants (including grandchildren) of cancelling parents to cancelled: they are claimed only
    # after upstream success, so otherwise remain queued forever and occupy deduplication slots. Run before parents leave cancelling.
    op.execute(
        sa.text(
            """
            WITH RECURSIVE doomed(task_id) AS (
                SELECT task_id FROM tasks WHERE status = 'cancelling'
                UNION
                SELECT tasks.task_id
                FROM tasks JOIN doomed ON tasks.dependency_task_id = doomed.task_id
                WHERE tasks.status = 'queued'
            )
            UPDATE tasks
            SET status = 'cancelled',
                cancelled_by = 'cascade',
                finished_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'queued' AND task_id IN (SELECT task_id FROM doomed)
            """
        )
    )
    # Existing cancelling rows have no worker to finish them; set cancelled. cancelled_by was recorded on entering cancelling.
    op.execute(
        sa.text(
            """
            UPDATE tasks
            SET status = 'cancelled',
                cancelled_by = COALESCE(cancelled_by, 'user'),
                finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'cancelling'
            """
        )
    )
    _drop_dedup_index()
    _create_dedup_index("'queued', 'running'")


def downgrade() -> None:
    _drop_dedup_index()
    _create_dedup_index("'queued', 'running', 'cancelling'")
