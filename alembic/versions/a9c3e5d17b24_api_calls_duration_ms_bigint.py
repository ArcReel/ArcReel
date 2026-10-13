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

# PostgreSQL int4 的毫秒上限，约 24.8 天。
_INT32_MAX = 2_147_483_647


def upgrade() -> None:
    # int4 装不下长期停在 pending 的行被结算时的墙钟毫秒数（超过约 24.8 天即溢出）。
    # SQLite 的整数本就是 64 位，这里只是让两种方言的声明类型对齐。
    with preserve_sqlite_indexes("api_calls"), op.batch_alter_table("api_calls") as batch_op:
        batch_op.alter_column(
            "duration_ms",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
        )


def downgrade() -> None:
    # 超出 int4 的耗时无法回填窄列：这些值只可能来自长期悬停后的结算，耗时不可信，置空后再收窄。
    op.execute(sa.text(f"UPDATE api_calls SET duration_ms = NULL WHERE duration_ms > {_INT32_MAX}"))
    with preserve_sqlite_indexes("api_calls"), op.batch_alter_table("api_calls") as batch_op:
        batch_op.alter_column(
            "duration_ms",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=True,
        )
