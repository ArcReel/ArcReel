"""api_calls 费用可空，新增未定价原因与缓存读写列

Revision ID: d4e8a1f6c392
Revises: a9c3e5d17b24
Create Date: 2026-10-14 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from lib.db.migration_helpers import preserve_sqlite_indexes

revision: str = "d4e8a1f6c392"
down_revision: str | Sequence[str] | None = "a9c3e5d17b24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_COLUMNS = ("unpriced_reason", "cache_read_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens")


def upgrade() -> None:
    # cost_amount 为 NULL 表示未定价，与 0（定价为零）区分；历史行保持原值，不回填。
    # 新增列也不回填：历史行没有缓存拆分与未定价原因的记录。input_tokens 仍记总输入（含缓存）。
    with preserve_sqlite_indexes("api_calls"), op.batch_alter_table("api_calls") as batch_op:
        batch_op.alter_column(
            "cost_amount",
            existing_type=sa.Float(),
            nullable=True,
            existing_server_default=sa.text("0.0"),
        )
        batch_op.add_column(sa.Column("unpriced_reason", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("cache_read_tokens", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("cache_write_5m_tokens", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("cache_write_1h_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    # 旧结构里费用不可空：未定价的行回退为 0，这是降级时唯一能保住行的取值。
    op.execute(sa.text("UPDATE api_calls SET cost_amount = 0.0 WHERE cost_amount IS NULL"))
    with preserve_sqlite_indexes("api_calls"), op.batch_alter_table("api_calls") as batch_op:
        for column in reversed(_NEW_COLUMNS):
            batch_op.drop_column(column)
        batch_op.alter_column(
            "cost_amount",
            existing_type=sa.Float(),
            nullable=False,
            existing_server_default=sa.text("0.0"),
        )
