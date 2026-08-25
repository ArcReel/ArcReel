"""add account center identity and one-time login tickets

Revision ID: f08c1d2e3a44
Revises: d82b14f6c921
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f08c1d2e3a44"
down_revision: str | Sequence[str] | None = "d82b14f6c921"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("account_center_sub", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("account_center_roles", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("account_center_synced_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("display_name", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("contact_email", sa.String(length=320), nullable=True))
        batch_op.create_unique_constraint("uq_users_account_center_sub", ["account_center_sub"])

    op.create_table(
        "account_center_login_tickets",
        sa.Column("ticket_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("account_center_sub", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("contact_email", sa.String(length=320), nullable=True),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("local_user_id", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["local_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ticket_hash"),
    )
    op.create_index(
        op.f("ix_account_center_login_tickets_expires_at"),
        "account_center_login_tickets",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_account_center_login_tickets_expires_at"), table_name="account_center_login_tickets")
    op.drop_table("account_center_login_tickets")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint("uq_users_account_center_sub", type_="unique")
        batch_op.drop_column("contact_email")
        batch_op.drop_column("display_name")
        batch_op.drop_column("account_center_synced_at")
        batch_op.drop_column("account_center_roles")
        batch_op.drop_column("account_center_sub")
