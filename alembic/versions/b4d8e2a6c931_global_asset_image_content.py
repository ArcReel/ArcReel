"""Content digests, identity, and remote locators for global asset-library images

Revision ID: b4d8e2a6c931
Revises: f7c3a9d15e42
Create Date: 2026-11-03 22:00:00.000000

Global-asset and derivative images may live in object storage and carry copied provider asset links.
All three new columns are nullable: existing rows lack these facts, use local library-storage files,
and receive no backfill.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4d8e2a6c931"
down_revision: str | Sequence[str] | None = "f7c3a9d15e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("assets", "asset_derivatives")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("image_digest", sa.String(length=64), nullable=True))
            batch.add_column(sa.Column("image_content_id", sa.String(length=64), nullable=True))
            batch.add_column(sa.Column("image_storage", sa.JSON(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("image_storage")
            batch.drop_column("image_content_id")
            batch.drop_column("image_digest")
