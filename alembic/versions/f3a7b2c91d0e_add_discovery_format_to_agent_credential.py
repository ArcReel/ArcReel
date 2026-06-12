"""add discovery_format to agent credential

Revision ID: f3a7b2c91d0e
Revises: 8b1e8a1290ca
Create Date: 2026-05-18
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "f3a7b2c91d0e"
down_revision = "8b1e8a1290ca"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_anthropic_credentials",
        sa.Column("discovery_format", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_anthropic_credentials", "discovery_format")
