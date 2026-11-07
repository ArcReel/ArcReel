"""Change api_calls.duration_ms to a 64-bit integer

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

# PostgreSQL int4 millisecond range, approximately 24.8 days maximum.
_INT32_MIN = -2_147_483_648
_INT32_MAX = 2_147_483_647


def upgrade() -> None:
    # int4 cannot hold settlement wall-clock milliseconds for long-pending rows (overflow beyond approximately 24.8 days).
    # SQLite integers are already 64-bit; this aligns declared types across dialects.
    with preserve_sqlite_indexes("api_calls"), op.batch_alter_table("api_calls") as batch_op:
        batch_op.alter_column(
            "duration_ms",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
        )


def downgrade() -> None:
    # Durations beyond int4 cannot fit the narrow column: long-pending settlement or large backward wall-clock jumps
    # produce untrustworthy durations, including negative values. Clear them before narrowing.
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
