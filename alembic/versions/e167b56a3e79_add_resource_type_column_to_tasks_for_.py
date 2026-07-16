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
    """跨方言安全 drop：DB 可能因历史迁移漂移而没建过该索引，避免 OperationalError。"""
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect in ("sqlite", "postgresql"):
        op.execute("DROP INDEX IF EXISTS idx_tasks_dedupe_active")
    else:
        op.drop_index("idx_tasks_dedupe_active", table_name="tasks")


def _collapse_duplicate_active_tasks_for_downgrade() -> None:
    """降级前折叠因移除 resource_type 产生的重复活动任务，避免窄索引重建时唯一键冲突。

    升级后不同资产类型同名（如角色和道具都叫「玉佩」）的活动 image_edit 任务可以并存；
    降级恢复不含 resource_type 的窄索引前，这些合并后撞键的分组只保留最早入队的一条，
    其余按既有 cancel 语义（见 ``TaskRepository.cancel_task``）软取消——不落 task_events，
    降级路径不依赖事件流回放；保留行、仅摘除其终态外的活动任务，不做硬删除。
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
    """新增 resource_type 列并纳入去重索引。

    只有 image_edit 任务写入该列（其余任务类型的 task_type 本身已按资源种类区分）；
    不同资产类型同名（如角色和道具都叫「玉佩」）时，原索引不看资源类型，会把两个
    不同资源的编辑误判为重复入队。
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
