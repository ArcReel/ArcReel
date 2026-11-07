"""Official sync table: fetched catalog data and sync state

Revision ID: b5d1e8f3a627
Revises: a7c3e91d4b58
Create Date: 2026-10-21 10:00:00.000000

Store the entire fetched catalog dataset in one row with dataset version, last successful sync time,
and latest failure reason for system diagnostics (docs/adr/0103). Existing instances have never synced,
so the table is empty and the catalog continues using its shipped snapshot.
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
