"""add foreign key to custom_provider_model

Revision ID: 96f747fa47d5
Revises: 5105eceaf7cb
Create Date: 2026-04-01 00:05:09.014782

"""

from collections.abc import Sequence

from alembic import op

revision: str = "96f747fa47d5"
down_revision: str | Sequence[str] | None = "5105eceaf7cb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_custom_provider_model_provider_id", "custom_provider", ["provider_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.drop_constraint("fk_custom_provider_model_provider_id", type_="foreignkey")
