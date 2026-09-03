"""create asset_derivatives table

Revision ID: 9f3c7a52d1b4
Revises: 8c2b1e7d4a90
Create Date: 2026-09-04 07:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f3c7a52d1b4"
down_revision: str | Sequence[str] | None = "8c2b1e7d4a90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "asset_derivatives",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("image_path", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_asset_derivative_asset_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "name", name="uq_asset_derivative_asset_name"),
    )
    with op.batch_alter_table("asset_derivatives", schema=None) as batch_op:
        batch_op.create_index("ix_asset_derivative_asset_id", ["asset_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("asset_derivatives", schema=None) as batch_op:
        batch_op.drop_index("ix_asset_derivative_asset_id")

    op.drop_table("asset_derivatives")
