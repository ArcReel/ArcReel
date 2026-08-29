"""add last provider response

Revision ID: 3b7c921d5e44
Revises: a1c7e94f0d23
Create Date: 2026-08-28 11:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3b7c921d5e44"
down_revision: str | Sequence[str] | None = "a1c7e94f0d23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("api_calls", sa.Column("last_provider_response", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("api_calls", "last_provider_response")
