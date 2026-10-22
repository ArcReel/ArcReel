"""官方同步表：官方同步取回的目录数据与同步状态

Revision ID: b5d1e8f3a627
Revises: a7c3e91d4b58
Create Date: 2026-10-21 10:00:00.000000

实例从目录数据仓库取回的整份数据集存进这张表，只有一行，带数据集版本；同时记上次成功同步的时间与最近一次
失败原因，供系统诊断信息使用（docs/adr/0103）。存量实例尚未同步过，新表为空，目录照旧使用出厂快照。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b5d1e8f3a627"
down_revision: str | Sequence[str] | None = "a7c3e91d4b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_official_sync",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("dataset_version", sa.String(length=32), nullable=True),
        sa.Column("catalog", sa.LargeBinary(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("catalog_official_sync")
