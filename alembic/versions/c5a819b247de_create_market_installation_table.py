"""create market_installation table

Revision ID: c5a819b247de
Revises: b4e8d2c61a57
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5a819b247de"
down_revision: str | Sequence[str] | None = "b4e8d2c61a57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_installation",
        sa.Column("custom_endpoint_id", sa.Integer(), nullable=False),
        sa.Column("source_key", sa.String(2048), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("installed_version", sa.Text(), nullable=False),
        sa.Column("installed_digest", sa.String(64), nullable=False),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["custom_endpoint_id"], ["custom_endpoint.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("custom_endpoint_id"),
        sa.UniqueConstraint("source_key", "slug"),
    )


def downgrade() -> None:
    op.drop_table("market_installation")
