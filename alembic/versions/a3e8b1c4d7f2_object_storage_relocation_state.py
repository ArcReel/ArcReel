"""对象存储连接新增收敛回本地持久化状态列

Revision ID: a3e8b1c4d7f2
Revises: 9c4f2a7e1d35
Create Date: 2026-11-28 15:00:00.000000

为 ``object_storage_connection`` 新增 ``relocating``（默认 ``false``）、``relocate_delete_remote``（可空）与
``relocate_mode``（可空，``switch`` / ``delete``），用于持久化旧桶收敛回本地流程中的阻断标记与用户是否删除
旧桶对象的选择，确保进程重启后可继续收敛或取消，且后台回收与扫盘严格服从 ``relocate_delete_remote``。

列变更使用原生 ``ALTER TABLE ADD COLUMN``，SQLite 上不重建表以保留 ``AUTOINCREMENT``。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3e8b1c4d7f2"
down_revision: str | Sequence[str] | None = "9c4f2a7e1d35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "object_storage_connection"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("relocating", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column(_TABLE, sa.Column("relocate_delete_remote", sa.Boolean(), nullable=True))
    op.add_column(_TABLE, sa.Column("relocate_mode", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "relocate_mode")
    op.drop_column(_TABLE, "relocate_delete_remote")
    op.drop_column(_TABLE, "relocating")
