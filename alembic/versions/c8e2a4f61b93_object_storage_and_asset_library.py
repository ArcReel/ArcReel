"""对象存储连接、素材库账号与推理凭证绑定表

Revision ID: c8e2a4f61b93
Revises: b5d1e8f3a627
Create Date: 2026-11-03 10:00:00.000000

用户自有对象存储的连接配置、供应商素材库的控制面账号（站点、区域、项目与控制面凭证），以及账号范围与
推理凭证之间的显式绑定。三张都是新表，存量实例为空，没有云配置的本地项目照常工作。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8e2a4f61b93"
down_revision: str | Sequence[str] | None = "b5d1e8f3a627"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "object_storage_connection",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("bucket", sa.String(length=255), nullable=False),
        sa.Column("access_key_id", sa.Text(), nullable=False),
        sa.Column("secret_access_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "asset_library_account",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("site", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("project_name", sa.String(length=128), nullable=False),
        sa.Column("access_key_id", sa.Text(), nullable=False),
        sa.Column("secret_access_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "asset_library_binding",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("credential_type", sa.String(length=16), nullable=False),
        sa.Column("credential_id", sa.Integer(), nullable=False),
        sa.Column("confirmed_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["asset_library_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_type", "credential_id", name="uq_asset_library_binding_credential"),
    )
    op.create_index("ix_asset_library_binding_account_id", "asset_library_binding", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_asset_library_binding_account_id", table_name="asset_library_binding")
    op.drop_table("asset_library_binding")
    op.drop_table("asset_library_account")
    op.drop_table("object_storage_connection")
