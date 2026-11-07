"""Provider asset links and object-storage relay-copy tables

Revision ID: aefa137b10ad
Revises: c8e2a4f61b93
Create Date: 2026-11-03 18:00:00.000000

Provider asset links map content versions to provider assets and hold registration tasks before asset IDs
exist. Relay copies record temporary objects written to user storage for provider image retrieval, for
cleanup after retention expires. Both tables are new and empty on existing instances.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "aefa137b10ad"
down_revision: str | Sequence[str] | None = "c8e2a4f61b93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_asset_link",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_name", sa.String(length=255), nullable=False),
        sa.Column("content_version_id", sa.String(length=64), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("locator_path", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("site", sa.String(length=16), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("provider_project_name", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("provider_asset_id", sa.Text(), nullable=True),
        sa.Column("provider_group_id", sa.Text(), nullable=True),
        sa.Column("submit_name", sa.String(length=64), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("check_error", sa.String(length=128), nullable=True),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("unlinked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "uq_provider_asset_link_selected",
        "provider_asset_link",
        ["project_name", "content_version_id", "account_id", "site", "region", "provider_project_name"],
        unique=True,
        sqlite_where=sa.text("selected = 1"),
        postgresql_where=sa.text("selected"),
    )
    op.create_index(
        "ix_provider_asset_link_content_version", "provider_asset_link", ["project_name", "content_version_id"]
    )
    op.create_index("ix_provider_asset_link_next_check_at", "provider_asset_link", ["next_check_at"])
    op.create_table(
        "object_storage_relay_object",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("bucket", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("link_id", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("url_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delete_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=128), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_object_storage_relay_object_state_next", "object_storage_relay_object", ["state", "next_attempt_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_object_storage_relay_object_state_next", table_name="object_storage_relay_object")
    op.drop_table("object_storage_relay_object")
    op.drop_index("ix_provider_asset_link_next_check_at", table_name="provider_asset_link")
    op.drop_index("ix_provider_asset_link_content_version", table_name="provider_asset_link")
    op.drop_index("uq_provider_asset_link_selected", table_name="provider_asset_link")
    op.drop_table("provider_asset_link")
