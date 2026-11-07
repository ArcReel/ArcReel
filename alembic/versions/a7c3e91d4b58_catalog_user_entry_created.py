"""User-entry table: record whether an entry was created by the user

Revision ID: a7c3e91d4b58
Revises: f4b8d2a6c913
Create Date: 2026-10-18 10:00:00.000000

Entries created by users who cannot find a model and fill in its details share a table with edited official
entry copies. When official data later contains that identity, distinguish them: offer newly created entries
Switch to official data and edited copies Restore official data. Existing copies all came from edits to
official entries, so default false is correct.
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
