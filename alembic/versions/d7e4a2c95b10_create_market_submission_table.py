"""create market_submission table

Revision ID: d7e4a2c95b10
Revises: b66f6d617e3f
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7e4a2c95b10"
down_revision: str | Sequence[str] | None = "b66f6d617e3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_submission",
        sa.Column("custom_endpoint_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("pr_url", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["custom_endpoint_id"], ["custom_endpoint.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("custom_endpoint_id"),
    )


def downgrade() -> None:
    op.drop_table("market_submission")
