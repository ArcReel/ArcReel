"""scope active task deduplication by user

Revision ID: 7f2c4d8a91b3
Revises: 26c2f9d11c8e
Create Date: 2026-08-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7f2c4d8a91b3"
down_revision: str | Sequence[str] | None = "26c2f9d11c8e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_dedup_index() -> None:
    bind = op.get_bind()
    if bind.dialect.name in ("sqlite", "postgresql"):
        op.execute("DROP INDEX IF EXISTS idx_tasks_dedupe_active")
    else:
        op.drop_index("idx_tasks_dedupe_active", table_name="tasks")


def _create_dedup_index(*, include_user: bool) -> None:
    columns: list[str | sa.TextClause] = ["project_name"]
    if include_user:
        columns.append("user_id")
    columns.extend(
        [
            "task_type",
            "resource_id",
            sa.text("COALESCE(script_file, '')"),
            sa.text("COALESCE(resource_type, '')"),
        ]
    )
    op.create_index(
        "idx_tasks_dedupe_active",
        "tasks",
        columns,
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'cancelling')"),
        sqlite_where=sa.text("status IN ('queued', 'running', 'cancelling')"),
    )


def upgrade() -> None:
    _drop_dedup_index()
    _create_dedup_index(include_user=True)


def downgrade() -> None:
    _drop_dedup_index()
    op.execute(
        sa.text(
            """
            UPDATE tasks
            SET status = 'cancelled',
                cancelled_by = 'system',
                finished_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE status IN ('queued', 'running', 'cancelling')
              AND task_id NOT IN (
                  SELECT task_id FROM (
                      SELECT task_id,
                             ROW_NUMBER() OVER (
                                 PARTITION BY project_name, task_type, resource_id,
                                              COALESCE(script_file, ''), COALESCE(resource_type, '')
                                 ORDER BY queued_at, task_id
                             ) AS rn
                      FROM tasks
                      WHERE status IN ('queued', 'running', 'cancelling')
                  ) ranked
                  WHERE rn = 1
              )
            """
        )
    )
    _create_dedup_index(include_user=False)
