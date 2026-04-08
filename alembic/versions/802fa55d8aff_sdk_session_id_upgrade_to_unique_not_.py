"""sdk_session_id upgrade to unique not null

Revision ID: 802fa55d8aff
Revises: e13e987e2170
Create Date: 2026-03-21 20:49:38.163219

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "802fa55d8aff"
down_revision: str | Sequence[str] | None = "e13e987e2170"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Delete ghost records where sdk_session_id IS NULL
    op.execute("DELETE FROM agent_sessions WHERE sdk_session_id IS NULL")

    # 2. Add UNIQUE + NOT NULL constraint (using batch_alter_table for SQLite compatibility)
    with op.batch_alter_table("agent_sessions", schema=None) as batch_op:
        batch_op.alter_column(
            "sdk_session_id",
            existing_type=sa.VARCHAR(),
            nullable=False,
        )
        batch_op.create_unique_constraint("uq_agent_sessions_sdk_session_id", ["sdk_session_id"])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("agent_sessions", schema=None) as batch_op:
        batch_op.drop_constraint("uq_agent_sessions_sdk_session_id", type_="unique")
        batch_op.alter_column(
            "sdk_session_id",
            existing_type=sa.VARCHAR(),
            nullable=True,
        )
