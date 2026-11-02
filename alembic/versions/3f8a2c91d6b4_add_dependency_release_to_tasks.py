"""add dependency_release to tasks

Revision ID: 3f8a2c91d6b4
Revises: b5d1e8f3a627
Create Date: 2026-10-28 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f8a2c91d6b4"
down_revision: str | Sequence[str] | None = "b5d1e8f3a627"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """新增 dependency_release 列（放行口径），只增列、无数据回填。

    参考生视频批量补齐按单元顺序串链时，前一个单元失败/取消不应拖死后续单元：
    该列标记依赖任务进入任意终态即可放行（NULL 保持旧口径——仅成功放行）。
    """
    op.add_column("tasks", sa.Column("dependency_release", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "dependency_release")
