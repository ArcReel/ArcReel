"""add audio_path to assets

Revision ID: d10f1df40f96
Revises: 3649100774fa
Create Date: 2026-07-31 14:46:01.193992

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d10f1df40f96"
down_revision: str | Sequence[str] | None = "3649100774fa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("audio_path", sa.String(length=500), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_column("audio_path")
