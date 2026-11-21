"""add agent session superseded_by pointer

Revision ID: 538db5c3ec76
Revises: 6a89c9ef8803
Create Date: 2026-08-10 14:39:56.296446

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "538db5c3ec76"
down_revision: str | Sequence[str] | None = "6a89c9ef8803"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("agent_sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("superseded_by", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("agent_sessions", schema=None) as batch_op:
        batch_op.drop_column("superseded_by")
