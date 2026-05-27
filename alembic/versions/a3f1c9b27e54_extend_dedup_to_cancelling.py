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
    """跨方言安全 drop：DB 可能因历史迁移漂移而没建过该索引，避免 OperationalError。"""
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "sqlite":
        op.execute("DROP INDEX IF EXISTS idx_tasks_dedupe_active")
    elif dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS idx_tasks_dedupe_active")
    else:
        op.drop_index("idx_tasks_dedupe_active", table_name="tasks")


def upgrade() -> None:
    """Recreate idx_tasks_dedupe_active with 'cancelling' in the partial WHERE.

    需要扩到 cancelling，避免 cancel running 后立刻重做创建并发任务——cancelling
    任务还在 worker 端响应 CancelledError 期间，partial unique 必须能拦住同资源的
    新入队（让 enqueue 走 dedup 返回 cancelling task_id），否则两个任务并发往同一
    输出文件写。
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
