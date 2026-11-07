"""Enforce object-storage connection singleton at database level

Revision ID: d5a9c3e71f28
Revises: b4d8e2a6c931
Create Date: 2026-11-04 10:00:00.000000

Add nullable ``slot`` and its unique index: current connection has ``slot = 1``, allowing one row.
Rows with null ``slot`` are retired connections retained only for still-referenced locators; never read
as current connections.

Previously query-then-insert allowed concurrent first saves to leave duplicates. Handle existing duplicates:

- Lowest ID becomes current, matching prior reads and formal-object/relay-copy/connection-change
  protection interpretation; migration does not change this.
- Keep other rows referenced by undeleted formal objects or relay copies as retired (null ``slot``),
  preserving locators/credentials. They were already noncurrent; referring objects still see mismatched connections.
- Delete remaining rows with no undeleted-object references. IDs are not recycled; old locators cannot
  thereby point to another bucket.

Use native ``ALTER TABLE ADD COLUMN`` / ``CREATE INDEX`` without rebuilding SQLite, preserving AUTOINCREMENT.
Downgrade removes index/column but keeps retired rows, selecting lowest-ID current as before migration.
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
