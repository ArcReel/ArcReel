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
    """Copy default_image_backend to new _t2i / _i2i keys, skipping each already-existing new key."""
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
    """Rollback deletes only new keys; the old key always remains."""
    op.execute("DELETE FROM system_setting WHERE key IN ('default_image_backend_t2i', 'default_image_backend_i2i')")
