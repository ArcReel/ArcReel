"""normalize agent credential base_url

Revision ID: c9fcbd98e62c
Revises: 9f3c7a52d1b4
Create Date: 2026-09-08 01:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9fcbd98e62c"
down_revision: str | Sequence[str] | None = "9f3c7a52d1b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MESSAGES_PATH = "/v1/messages"


def _normalize(raw: str) -> str:
    """与 ``lib.config.url_utils.normalize_anthropic_base_url`` 同一规则的冻结副本。

    去首尾空白、去尾部斜杠、末尾恰好是 ``/v1/messages`` 时去掉这一段并再去一次尾斜杠。
    含 query / fragment / userinfo 的值同样只做这三步，它们在测试连接时会被入口校验拒绝。
    """
    stripped = raw.strip().rstrip("/")
    if stripped.endswith(_MESSAGES_PATH):
        stripped = stripped[: -len(_MESSAGES_PATH)].rstrip("/")
    return stripped


def upgrade() -> None:
    """把存量 Agent 凭证的 base_url 归一到「存储值即调用值」形态。

    运行时把 base_url 原样写进 ``ANTHROPIC_BASE_URL``、由 Claude CLI 固定追加 ``/v1/messages``；
    旧版本入库前不归一，带 ``/v1/messages`` 后缀或尾斜杠的行会让测试连接与运行时分叉。
    只改归一后值有变化的行。
    """
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, base_url FROM agent_anthropic_credentials")).fetchall()
    for row in rows:
        raw = row.base_url or ""
        normalized = _normalize(raw)
        if normalized == raw:
            continue
        bind.execute(
            sa.text("UPDATE agent_anthropic_credentials SET base_url = :base_url WHERE id = :id"),
            {"base_url": normalized, "id": row.id},
        )


def downgrade() -> None:
    """归一是幂等的纯数据整理，旧版本同样接受归一后的值，无需回退。"""
