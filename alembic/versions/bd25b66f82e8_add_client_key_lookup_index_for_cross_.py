"""add client_key lookup index for cross-session idempotency

Revision ID: bd25b66f82e8
Revises: 3b841838ac16
Create Date: 2026-07-07 13:14:23.784371

client_key 唯一索引按 (session_id, client_key) 分区，覆盖不到 session_id
尚不存在的新会话受理去重；跨会话按 client_key 的兜底查询需要以 client_key
打头的检索索引。部分索引（client_key IS NOT NULL）避免为占多数的空键行建项。

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bd25b66f82e8"
down_revision: str | Sequence[str] | None = "3b841838ac16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_agent_event_log_client_key",
        "agent_session_event_log",
        ["client_key"],
        unique=False,
        postgresql_where=sa.text("client_key IS NOT NULL"),
        sqlite_where=sa.text("client_key IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_agent_event_log_client_key", table_name="agent_session_event_log")
