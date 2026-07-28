"""add session event log table

Revision ID: f3d21ac90b17
Revises: a7a9749a1ae0
Create Date: 2026-07-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3d21ac90b17"
down_revision: str | Sequence[str] | None = "a7a9749a1ae0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "agent_session_event_log",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("entry_type", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("client_key", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(), server_default="default", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "seq"),
    )
    with op.batch_alter_table("agent_session_event_log", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_agent_session_event_log_user_id"),
            ["user_id"],
            unique=False,
        )
        batch_op.create_index(
            "uq_agent_event_log_client_key",
            ["session_id", "client_key"],
            unique=True,
            postgresql_where=sa.text("client_key IS NOT NULL"),
            sqlite_where=sa.text("client_key IS NOT NULL"),
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("agent_session_event_log", schema=None) as batch_op:
        batch_op.drop_index("uq_agent_event_log_client_key")
        batch_op.drop_index(batch_op.f("ix_agent_session_event_log_user_id"))
    op.drop_table("agent_session_event_log")
