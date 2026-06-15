"""add access_key secret_key to provider_credential

Revision ID: 7fb52d06b50e
Revises: a3f1c9b27e54
Create Date: 2026-06-15 14:41:36.143309

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7fb52d06b50e"
down_revision: str | Sequence[str] | None = "a3f1c9b27e54"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    Additive nullable columns for providers that need two secret strings
    (Kling: access_key + secret_key). No backfill — existing rows are correct
    with NULL, and neither column participates in any WHERE/filter.
    """
    with op.batch_alter_table("provider_credential", schema=None) as batch_op:
        batch_op.add_column(sa.Column("access_key", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("secret_key", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("provider_credential", schema=None) as batch_op:
        batch_op.drop_column("secret_key")
        batch_op.drop_column("access_key")
