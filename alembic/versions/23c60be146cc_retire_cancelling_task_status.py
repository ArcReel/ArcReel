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
    # cancelling 父行的排队下游（含孙辈）按级联取消落终态：下游只在上游 succeeded 后被认领，
    # 不级联会永远停在 queued 并占住去重槽位。须在父行离开 cancelling 之前执行。
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
    # 存量 cancelling 行已无人接手，收敛为 cancelled；cancelled_by 在进入 cancelling 时已写入。
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
