"""内置供应商模型表：官方收录模型的启用状态与用户添加的模型

Revision ID: e3a7c91f5d24
Revises: b25cfcfb8bad
Create Date: 2026-10-16 10:00:00.000000

内置供应商的模型可以逐个启用、停用，用户也可以按 ID 添加模型（docs/adr/0099、0106）。官方收录的模型没有
行时，条目未下架即为启用，所以原先可见的模型迁移后都是启用。原先在注册表里隐藏的 3 个 Agnes 旧模型写入停用行，
迁移后为停用；存量数据里没有用户添加的模型。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "e3a7c91f5d24"
down_revision: str | Sequence[str] | None = "b25cfcfb8bad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 改造前注册表里 ``hidden=True`` 的模型：(供应商, 模型键)。
_HIDDEN_MODELS: tuple[tuple[str, str], ...] = (
    ("agnes", "agnes-2.0-flash"),
    ("agnes", "agnes-image-2.1-flash"),
    ("agnes", "agnes-video-v2.0"),
)


def upgrade() -> None:
    table = op.create_table(
        "builtin_provider_model",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("user_added", sa.Boolean(), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=True),
        sa.Column("multiplier", sa.Float(), nullable=False, server_default=sa.text("1")),
        sa.Column("entry_binding", sa.String(length=255), nullable=True),
        sa.Column("capability_overrides", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "model_id", name="uq_builtin_provider_model"),
    )
    op.create_index("ix_builtin_provider_model_provider_id", "builtin_provider_model", ["provider_id"])
    now = datetime.now(UTC)
    op.bulk_insert(
        table,
        [
            {
                "provider_id": provider_id,
                "model_id": model_id,
                "enabled": False,
                "user_added": False,
                "multiplier": 1.0,
                "created_at": now,
                "updated_at": now,
            }
            for provider_id, model_id in _HIDDEN_MODELS
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_builtin_provider_model_provider_id", table_name="builtin_provider_model")
    op.drop_table("builtin_provider_model")
