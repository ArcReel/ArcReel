"""add submitted_base_url to tasks

Revision ID: d4f8b1c73a20
Revises: 9c41ad2f7be5
Create Date: 2026-08-12 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from lib.db.migration_helpers import preserve_sqlite_indexes

# revision identifiers, used by Alembic.
revision: str = "d4f8b1c73a20"
down_revision: str | Sequence[str] | None = "9c41ad2f7be5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("submitted_base_url", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with preserve_sqlite_indexes("tasks"), op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("submitted_base_url")
