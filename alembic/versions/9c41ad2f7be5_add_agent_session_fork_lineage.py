"""add agent session fork lineage

Revision ID: 9c41ad2f7be5
Revises: 538db5c3ec76
Create Date: 2026-08-11 09:12:04.118273

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9c41ad2f7be5"
down_revision: str | Sequence[str] | None = "538db5c3ec76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fork_parent_session_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("fork_anchor_uuid", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_sessions", schema=None) as batch_op:
        batch_op.drop_column("fork_anchor_uuid")
        batch_op.drop_column("fork_parent_session_id")
