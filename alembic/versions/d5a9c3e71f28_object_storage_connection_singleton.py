"""对象存储连接在数据库层保证单例

Revision ID: d5a9c3e71f28
Revises: b4d8e2a6c931
Create Date: 2026-11-04 10:00:00.000000

新增可为空的 ``slot`` 列与它的唯一索引：当前连接 ``slot = 1``，同一时刻只能有一行；``slot`` 为空的行是
退役连接，只为保留仍被引用的连接定位而存在，不会被当作当前连接读出。

此前保存连接是先查后插，并发首次保存可能留下多条记录。迁移前已存在多条记录时按以下规则处理：

- ID 最小的一条成为当前连接。此前读取连接一直取 ID 最小的那条，正式对象、中转副本与连接变更保护
  按它解释，迁移不改变这一点。
- 其余记录中，仍被未删除的正式素材对象或中转副本引用的保留为退役连接（``slot`` 为空），定位与凭证
  原样留存；它们此前就不是当前连接，引用它们的对象照旧按连接不一致处理。
- 其余没有任何未删除对象引用的记录删除。连接 ID 不回收，删除不会让旧定位指向另一个桶。

列与索引都用原生 ``ALTER TABLE ADD COLUMN`` / ``CREATE INDEX``，SQLite 上不重建表，保留表的 AUTOINCREMENT。
降级删除索引与列；退役连接仍保留，降级后按 ID 最小取当前连接，与迁移前的读取规则一致。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5a9c3e71f28"
down_revision: str | Sequence[str] | None = "b4d8e2a6c931"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "object_storage_connection"
_INDEX = "uq_object_storage_connection_slot"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("slot", sa.Integer(), nullable=True))
    bind = op.get_bind()
    ids = [row[0] for row in bind.execute(sa.text(f"SELECT id FROM {_TABLE} ORDER BY id"))]
    if ids:
        current, *others = ids
        bind.execute(sa.text(f"UPDATE {_TABLE} SET slot = 1 WHERE id = :id"), {"id": current})
        referenced = {
            row[0]
            for row in bind.execute(
                sa.text(
                    "SELECT connection_id FROM object_storage_formal_object WHERE state <> 'deleted' "
                    "UNION SELECT connection_id FROM object_storage_relay_object WHERE state <> 'deleted'"
                )
            )
        }
        for connection_id in others:
            if connection_id not in referenced:
                bind.execute(sa.text(f"DELETE FROM {_TABLE} WHERE id = :id"), {"id": connection_id})
    op.create_index(_INDEX, _TABLE, ["slot"], unique=True)


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_column(_TABLE, "slot")
