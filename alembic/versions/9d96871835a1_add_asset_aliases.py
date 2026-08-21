"""add asset aliases

Revision ID: 9d96871835a1
Revises: ca8d7e31b6f2
Create Date: 2026-08-21 18:32:16.616654

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d96871835a1"
down_revision: str | Sequence[str] | None = "ca8d7e31b6f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "asset_aliases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("alias", sa.String(length=200), nullable=False),
        sa.Column("comparison_key", sa.String(length=200), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("origin IN ('catalog', 'local')", name="ck_asset_alias_origin"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "comparison_key", name="uq_asset_alias_comparison_key"),
    )
    with op.batch_alter_table("asset_aliases", schema=None) as batch_op:
        batch_op.create_index("ix_asset_alias_comparison_key", ["comparison_key"], unique=False)
        batch_op.create_index(batch_op.f("ix_asset_aliases_asset_id"), ["asset_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("asset_aliases", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_asset_aliases_asset_id"))
        batch_op.drop_index("ix_asset_alias_comparison_key")
    op.drop_table("asset_aliases")
