"""add provider_endpoint to tasks

Revision ID: c4a91f7d2b18
Revises: b7f2c41d9a30
Create Date: 2026-08-05 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a91f7d2b18"
down_revision: str | Sequence[str] | None = "b7f2c41d9a30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("provider_endpoint", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("provider_endpoint")
