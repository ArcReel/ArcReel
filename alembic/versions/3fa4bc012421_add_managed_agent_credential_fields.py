"""add managed agent credential fields

Revision ID: 3fa4bc012421
Revises: ea80b1b9136c
Create Date: 2026-08-25 12:42:37.231636

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3fa4bc012421'
down_revision: str | Sequence[str] | None = 'ea80b1b9136c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Track centrally managed Agent credentials and their cloud revision."""
    with op.batch_alter_table("agent_anthropic_credentials", schema=None) as batch_op:
        batch_op.add_column(sa.Column("management_source", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("management_revision", sa.Integer(), nullable=True))
        batch_op.create_index(
            "uq_agent_credential_user_management_source",
            ["user_id", "management_source"],
            unique=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_anthropic_credentials", schema=None) as batch_op:
        batch_op.drop_index("uq_agent_credential_user_management_source")
        batch_op.drop_column("management_revision")
        batch_op.drop_column("management_source")
