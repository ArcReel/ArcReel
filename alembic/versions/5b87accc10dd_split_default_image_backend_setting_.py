"""split default_image_backend setting into t2i and i2i

Revision ID: 5b87accc10dd
Revises: eedf0aa985e6
Create Date: 2026-05-02 21:51:02.087612

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5b87accc10dd"
down_revision: str | Sequence[str] | None = "eedf0aa985e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """把已有 default_image_backend 的值复制到 _t2i / _i2i 两条新 key（若新 key 已存在则跳过该条）。"""
    bind = op.get_bind()
    legacy_row = bind.execute(
        sa.text("SELECT value FROM system_setting WHERE key = 'default_image_backend'")
    ).fetchone()
    if legacy_row is None:
        return

    legacy_value = legacy_row[0]

    for new_key in ("default_image_backend_t2i", "default_image_backend_i2i"):
        existing = bind.execute(sa.text("SELECT 1 FROM system_setting WHERE key = :k").bindparams(k=new_key)).fetchone()
        if existing:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO system_setting (key, value, updated_at) VALUES (:k, :v, CURRENT_TIMESTAMP)"
            ).bindparams(k=new_key, v=legacy_value)
        )


def downgrade() -> None:
    """回滚仅删除新 key；旧 key 始终保留。"""
    op.execute("DELETE FROM system_setting WHERE key IN ('default_image_backend_t2i', 'default_image_backend_i2i')")
