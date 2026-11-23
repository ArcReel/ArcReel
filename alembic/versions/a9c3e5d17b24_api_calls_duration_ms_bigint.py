"""api_calls.duration_ms 改为 64 位整数

Revision ID: a9c3e5d17b24
Revises: 015359a572fa
Create Date: 2026-10-13 14:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from lib.db.migration_helpers import preserve_sqlite_indexes

revision: str = "a9c3e5d17b24"
down_revision: str | Sequence[str] | None = "015359a572fa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INT32_MIN = -2_147_483_648
_INT32_MAX = 2_147_483_647


def upgrade() -> None:
    with preserve_sqlite_indexes("api_calls"), op.batch_alter_table("api_calls") as batch_op:
        batch_op.alter_column(
            "duration_ms",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
        )


def downgrade() -> None:
    op.execute(
        sa.text(f"UPDATE api_calls SET duration_ms = NULL WHERE duration_ms NOT BETWEEN {_INT32_MIN} AND {_INT32_MAX}")
    )
    with preserve_sqlite_indexes("api_calls"), op.batch_alter_table("api_calls") as batch_op:
        batch_op.alter_column(
            "duration_ms",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=True,
        )
