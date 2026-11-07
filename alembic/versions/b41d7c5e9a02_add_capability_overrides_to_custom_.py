"""add capability_overrides to custom provider model

Revision ID: b41d7c5e9a02
Revises: e167b56a3e79
Create Date: 2026-07-25 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b41d7c5e9a02"
down_revision: str | Sequence[str] | None = "e167b56a3e79"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add model-level capability overrides (sparse JSON; NULL follows all system determinations)."""
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.add_column(sa.Column("capability_overrides", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.drop_column("capability_overrides")
