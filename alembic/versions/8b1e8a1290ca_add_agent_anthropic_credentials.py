"""add agent anthropic credentials

Revision ID: 8b1e8a1290ca
Revises: 4c643f3ff5b9
Create Date: 2026-05-11 11:51:36.644592

"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b1e8a1290ca"
down_revision: str | Sequence[str] | None = "4c643f3ff5b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_USER_ID = "default"
_LEGACY_KEYS = (
    "anthropic_api_key",
    "anthropic_base_url",
    "anthropic_model",
    "anthropic_default_haiku_model",
    "anthropic_default_sonnet_model",
    "anthropic_default_opus_model",
    "claude_code_subagent_model",
)


def upgrade() -> None:
    op.create_table(
        "agent_anthropic_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False, server_default=DEFAULT_USER_ID),
        sa.Column("preset_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("haiku_model", sa.String(length=128), nullable=True),
        sa.Column("sonnet_model", sa.String(length=128), nullable=True),
        sa.Column("opus_model", sa.String(length=128), nullable=True),
        sa.Column("subagent_model", sa.String(length=128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("agent_anthropic_credentials", schema=None) as batch_op:
        batch_op.create_index("ix_agent_credential_user", ["user_id"], unique=False)
        batch_op.create_index(
            "uq_agent_credential_one_active_per_user",
            ["user_id"],
            unique=True,
            sqlite_where=sa.text("is_active = 1"),
            postgresql_where=sa.text("is_active"),
        )

    # ── 数据迁移：旧 system_settings 中 anthropic_* → 一条 __custom__ active 记录 ──
    bind = op.get_bind()
    try:
        rows = bind.execute(
            sa.text("SELECT key, value FROM system_settings WHERE key IN :keys").bindparams(
                sa.bindparam("keys", expanding=True)
            ),
            {"keys": list(_LEGACY_KEYS)},
        ).fetchall()
        settings = {r.key: r.value for r in rows if r.value}

        if settings.get("anthropic_api_key"):
            now = datetime.now(UTC)
            bind.execute(
                sa.text("""
                    INSERT INTO agent_anthropic_credentials
                      (user_id, preset_id, display_name, base_url, api_key,
                       model, haiku_model, sonnet_model, opus_model, subagent_model,
                       is_active, created_at, updated_at)
                    VALUES (:user_id, '__custom__', 'Migrated', :base_url, :api_key,
                            :model, :haiku, :sonnet, :opus, :subagent,
                            :is_active, :now, :now)
                """),
                {
                    "user_id": DEFAULT_USER_ID,
                    "base_url": settings.get("anthropic_base_url", ""),
                    "api_key": settings["anthropic_api_key"],
                    "model": settings.get("anthropic_model"),
                    "haiku": settings.get("anthropic_default_haiku_model"),
                    "sonnet": settings.get("anthropic_default_sonnet_model"),
                    "opus": settings.get("anthropic_default_opus_model"),
                    "subagent": settings.get("claude_code_subagent_model"),
                    "is_active": True,
                    "now": now,
                },
            )
    except Exception as exc:  # noqa: BLE001
        # 数据迁移失败不阻塞 schema 升级；用户可在 UI 里手动建
        import logging
        import sys

        msg = f"agent_anthropic_credentials data migration skipped: {exc}"
        logging.getLogger(__name__).warning(msg)
        print(f"[alembic] WARNING: {msg}", file=sys.stderr)


def downgrade() -> None:
    with op.batch_alter_table("agent_anthropic_credentials", schema=None) as batch_op:
        batch_op.drop_index("uq_agent_credential_one_active_per_user")
        batch_op.drop_index("ix_agent_credential_user")
    op.drop_table("agent_anthropic_credentials")
