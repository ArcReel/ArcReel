"""add resource_type column to tasks for image_edit dedup

Revision ID: e167b56a3e79
Revises: bd25b66f82e8
Create Date: 2026-07-16 17:15:46.753303

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e167b56a3e79"
down_revision: str | Sequence[str] | None = "bd25b66f82e8"
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


def _collapse_duplicate_active_tasks_for_downgrade() -> None:
    """Collapse active duplicates created by removing resource_type before rebuilding the narrower unique index.

    After upgrade, same-name assets of different types (e.g. character and prop both named 玉佩) may
    have active image_edit tasks. Before restoring the narrow resource_type-free index, retain only
    the earliest queued task per newly colliding group; soft-cancel others using existing semantics
    (``TaskRepository.cancel_task``): change status directly, without terminal events. Downgrade needs
    no event notifications. Keep rows, remove only nonterminal duplicates from the active set; no hard deletion.
    """
    op.execute(
        sa.text(
            """
            UPDATE tasks
            SET status = 'cancelled',
                cancelled_by = 'system',
                error_message = 'resource_type 列降级：与其他资源类型的同名活动任务合并去重',
                finished_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE status IN ('queued', 'running', 'cancelling')
              AND task_id NOT IN (
                  SELECT task_id FROM (
                      SELECT task_id,
                             ROW_NUMBER() OVER (
                                 PARTITION BY project_name, task_type, resource_id, COALESCE(script_file, '')
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


def upgrade() -> None:
    """Add resource_type and include it in the deduplication index.

    Only image_edit sets it (other task types already distinguish resource kinds through task_type).
    For same-name different-type assets (e.g. character and prop both 玉佩), the old index ignores
    resource type and incorrectly treats edits of distinct resources as duplicate enqueue attempts.
    """
    op.add_column("tasks", sa.Column("resource_type", sa.String(), nullable=True))
    _drop_dedup_index_if_exists()
    op.create_index(
        "idx_tasks_dedupe_active",
        "tasks",
        [
            "project_name",
            "task_type",
            "resource_id",
            sa.text("COALESCE(script_file, '')"),
            sa.text("COALESCE(resource_type, '')"),
        ],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'cancelling')"),
        sqlite_where=sa.text("status IN ('queued', 'running', 'cancelling')"),
    )


def downgrade() -> None:
    """Restore dedup index without resource_type, then drop the column."""
    _drop_dedup_index_if_exists()
    _collapse_duplicate_active_tasks_for_downgrade()
    op.create_index(
        "idx_tasks_dedupe_active",
        "tasks",
        ["project_name", "task_type", "resource_id", sa.text("COALESCE(script_file, '')")],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'cancelling')"),
        sqlite_where=sa.text("status IN ('queued', 'running', 'cancelling')"),
    )
    op.drop_column("tasks", "resource_type")
