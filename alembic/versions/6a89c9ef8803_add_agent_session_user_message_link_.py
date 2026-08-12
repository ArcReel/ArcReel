"""add agent session user message link table

Revision ID: 6a89c9ef8803
Revises: c4a91f7d2b18
Create Date: 2026-08-10 13:19:53.688771

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6a89c9ef8803"
down_revision: str | Sequence[str] | None = "c4a91f7d2b18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "agent_session_user_message_links",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("user_entry_uuid", sa.String(), nullable=False),
        sa.Column("sdk_entry_uuid", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(), server_default="default", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "user_entry_uuid"),
    )
    with op.batch_alter_table("agent_session_user_message_links", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_agent_session_user_message_links_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("agent_session_user_message_links", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agent_session_user_message_links_user_id"))

    op.drop_table("agent_session_user_message_links")
