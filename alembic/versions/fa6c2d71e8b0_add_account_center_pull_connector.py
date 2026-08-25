"""add account center pull connector

Revision ID: fa6c2d71e8b0
Revises: f19d7b4a2c61
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "fa6c2d71e8b0"
down_revision: str | Sequence[str] | None = "f19d7b4a2c61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("account_center_login_tickets") as batch_op:
        batch_op.add_column(sa.Column("device_id", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("device_token_encrypted", sa.String(), nullable=True))

    with op.batch_alter_table("provider_credential") as batch_op:
        batch_op.add_column(sa.Column("management_source", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("management_revision", sa.BigInteger(), nullable=True))

    op.create_table(
        "account_center_connections",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("account_center_sub", sa.String(), nullable=False),
        sa.Column("device_id", sa.String(length=200), nullable=False),
        sa.Column("device_token_encrypted", sa.String(), nullable=False),
        sa.Column("config_revision", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(length=16), nullable=True),
        sa.Column("last_sync_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("account_center_sub"),
        sa.UniqueConstraint("device_id"),
    )


def downgrade() -> None:
    op.drop_table("account_center_connections")
    with op.batch_alter_table("provider_credential") as batch_op:
        batch_op.drop_column("management_revision")
        batch_op.drop_column("management_source")
    with op.batch_alter_table("account_center_login_tickets") as batch_op:
        batch_op.drop_column("device_token_encrypted")
        batch_op.drop_column("device_id")
