"""add account scoped managed provider config

Revision ID: 78d31ac4f962
Revises: 3fa4bc012421
Create Date: 2026-08-25 16:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "78d31ac4f962"
down_revision: str | Sequence[str] | None = "3fa4bc012421"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "managed_provider_config",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False),
        sa.Column("management_source", sa.String(length=32), nullable=False),
        sa.Column("management_revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "provider", "key", "management_source", name="uq_managed_provider_config_identity"
        ),
    )
    op.create_index(
        "ix_managed_provider_config_user_provider",
        "managed_provider_config",
        ["user_id", "provider"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_managed_provider_config_user_provider", table_name="managed_provider_config")
    op.drop_table("managed_provider_config")
