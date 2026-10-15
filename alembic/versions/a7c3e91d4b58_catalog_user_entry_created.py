"""用户条目表：记录条目是否由用户新建

Revision ID: a7c3e91d4b58
Revises: f4b8d2a6c913
Create Date: 2026-10-18 10:00:00.000000

用户在目录里找不到模型、手动填写模型信息时新建的条目，与用户改过官方条目的副本存在同一张表。日后官方数据
收录同一身份的条目时，两者要区分开：新建的条目提示「改用官方数据」，改过的条目提示「恢复官方数据」。
存量副本都是用户改过官方条目产生的，默认值 false 即正确。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c3e91d4b58"
down_revision: str | Sequence[str] | None = "f4b8d2a6c913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("catalog_user_entry", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_created", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("catalog_user_entry", schema=None) as batch_op:
        batch_op.drop_column("user_created")
