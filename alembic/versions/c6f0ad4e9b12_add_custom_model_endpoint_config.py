"""add endpoint-specific config to custom provider models

Revision ID: c6f0ad4e9b12
Revises: b7f2c41d9a30
Create Date: 2026-08-03 10:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c6f0ad4e9b12"
down_revision: str | Sequence[str] | None = "b7f2c41d9a30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("custom_provider_model") as batch_op:
        batch_op.add_column(sa.Column("endpoint_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("custom_provider_model") as batch_op:
        batch_op.drop_column("endpoint_config")
