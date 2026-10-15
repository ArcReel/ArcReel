"""Agent 凭证新增倍率与档位的条目绑定

Revision ID: b25cfcfb8bad
Revises: c7e2a9d4b6f1
Create Date: 2026-10-15 20:00:00.000000

Agent 会话按凭证计价（docs/adr/0102）：

- ``multiplier``：Agent 凭证倍率，乘在这条凭证上每次 Agent 调用的参考费用上；存量为 1。为 0 时费用记 0，不查价
  （docs/adr/0100）。
- ``tier_bindings``：档位（``model`` / ``haiku`` / ``sonnet`` / ``opus`` / ``subagent``）到条目绑定（「模型厂商/官方模型名」）
  的映射；存量为空，即全部档位按模型名对应条目。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b25cfcfb8bad"
down_revision: str | Sequence[str] | None = "c7e2a9d4b6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_anthropic_credentials", schema=None) as batch_op:
        batch_op.add_column(sa.Column("multiplier", sa.Float(), nullable=False, server_default=sa.text("1")))
        batch_op.add_column(sa.Column("tier_bindings", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_anthropic_credentials", schema=None) as batch_op:
        batch_op.drop_column("tier_bindings")
        batch_op.drop_column("multiplier")
