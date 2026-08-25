"""add arcreel cloud identity session

Revision ID: ea80b1b9136c
Revises: fa6c2d71e8b0
Create Date: 2026-08-25 10:42:56.948327

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ea80b1b9136c"
down_revision: str | Sequence[str] | None = "fa6c2d71e8b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("arcreel_cloud_sub", sa.String(), nullable=True))
        batch_op.create_unique_constraint("uq_users_arcreel_cloud_sub", ["arcreel_cloud_sub"])

    op.create_table(
        "arcreel_cloud_sessions",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("cloud_user_sub", sa.String(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.String(), nullable=False),
        sa.Column("config_revision", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(length=16), nullable=True),
        sa.Column("last_sync_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("cloud_user_sub", name="uq_arcreel_cloud_sessions_cloud_user_sub"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("arcreel_cloud_sessions")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_arcreel_cloud_sub", type_="unique")
        batch_op.drop_column("arcreel_cloud_sub")
