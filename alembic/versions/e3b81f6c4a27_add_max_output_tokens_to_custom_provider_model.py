"""add max_output_tokens to custom_provider_model

Revision ID: e3b81f6c4a27
Revises: d7e4a2c95b10
Create Date: 2026-10-01 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3b81f6c4a27"
down_revision: str | Sequence[str] | None = "d7e4a2c95b10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.add_column(sa.Column("max_output_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.drop_column("max_output_tokens")
