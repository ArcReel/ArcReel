"""用户条目表：用户修改的模型条目副本

Revision ID: f4b8d2a6c913
Revises: e3a7c91f5d24
Create Date: 2026-10-17 10:00:00.000000

用户修改模型信息时，整个条目的副本存进这张表，每个条目一行；有行的条目停止跟随官方（docs/adr/0099）。
存量数据里没有用户修改，新表为空。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4b8d2a6c913"
down_revision: str | Sequence[str] | None = "e3a7c91f5d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_user_entry",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("maker", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("entry", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("maker", "name", "media_type", name="uq_catalog_user_entry"),
    )


def downgrade() -> None:
    op.drop_table("catalog_user_entry")
