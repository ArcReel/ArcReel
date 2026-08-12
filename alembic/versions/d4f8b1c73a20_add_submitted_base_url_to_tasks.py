"""add submitted_base_url to tasks

Revision ID: d4f8b1c73a20
Revises: 9c41ad2f7be5
Create Date: 2026-08-12 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4f8b1c73a20"
down_revision: str | Sequence[str] | None = "9c41ad2f7be5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("submitted_base_url", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema.

    SQLite 上 drop_column 走 batch 重建表，而 SQLAlchemy 反射不出 ``idx_tasks_dedupe_active``
    这种带表达式的 partial unique 索引，重建后它会静默消失、去重闸失效（同资源可并发起两个
    活动任务）。重建前抓 sqlite_master 里的原始 DDL、重建后原样执行，避免在本文件里复制一份
    会随后续迁移漂移的索引定义。PostgreSQL 走原生 ALTER 不重建表，索引不受影响。
    """
    bind = op.get_bind()
    dedupe_index_ddl: str | None = None
    if bind.dialect.name == "sqlite":
        dedupe_index_ddl = bind.execute(
            sa.text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_tasks_dedupe_active'")
        ).scalar()

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("submitted_base_url")

    if dedupe_index_ddl:
        op.execute(dedupe_index_ddl)
