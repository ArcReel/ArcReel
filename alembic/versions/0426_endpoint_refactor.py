"""rename api_format→discovery_format, media_type→endpoint

Revision ID: 0426endpointrefactor
Revises: a89021f43d52
Create Date: 2026-04-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0426endpointrefactor"
down_revision: str | Sequence[str] | None = "a89021f43d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (api_format, media_type) → endpoint
_UPGRADE_ENDPOINT_MAP = {
    ("openai", "text"): "openai-chat",
    ("openai", "image"): "openai-images",
    ("openai", "video"): "openai-video",
    ("google", "text"): "gemini-generate",
    ("google", "image"): "gemini-image",
    ("google", "video"): "openai-video",  # 兜底：用 OpenAI SDK 路径，比 newapi-video 在中转站生态更通用
    ("newapi", "text"): "openai-chat",
    ("newapi", "image"): "openai-images",
    ("newapi", "video"): "newapi-video",
}

# api_format → discovery_format
_UPGRADE_DISCOVERY_MAP = {
    "openai": "openai",
    "google": "google",
    "newapi": "openai",
}

# 反向：endpoint → (api_format, media_type)。downgrade 用。
_DOWNGRADE_MAP = {
    "openai-chat": ("openai", "text"),
    "gemini-generate": ("google", "text"),
    "openai-images": ("openai", "image"),
    "gemini-image": ("google", "image"),
    "openai-video": ("openai", "video"),
    "newapi-video": ("newapi", "video"),
}


def upgrade() -> None:
    bind = op.get_bind()

    # 1) provider 表：add 新列
    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.add_column(sa.Column("discovery_format", sa.String(length=32), nullable=True))

    # 2) provider 回填：每个 api_format 一条 UPDATE，最后 fail-loud 校验全量覆盖
    provider_total = bind.execute(sa.text("SELECT COUNT(*) FROM custom_provider")).scalar() or 0
    provider_mapped = 0
    for src, dst in _UPGRADE_DISCOVERY_MAP.items():
        result = bind.execute(
            sa.text("UPDATE custom_provider SET discovery_format = :dst WHERE api_format = :src"),
            {"src": src, "dst": dst},
        )
        provider_mapped += result.rowcount or 0
    if provider_mapped != provider_total:
        raise RuntimeError(f"custom_provider: {provider_total - provider_mapped} 条记录的 api_format 不在迁移映射中")

    # 3) model 表：add endpoint
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.add_column(sa.Column("endpoint", sa.String(length=32), nullable=True))

    # 4) model 回填：按 (api_format, media_type) 组合 ≤9 条 UPDATE，fail-loud 校验
    model_total = bind.execute(sa.text("SELECT COUNT(*) FROM custom_provider_model")).scalar() or 0
    model_mapped = 0
    for (api_format, media_type), endpoint in _UPGRADE_ENDPOINT_MAP.items():
        result = bind.execute(
            sa.text(
                "UPDATE custom_provider_model SET endpoint = :ep "
                "WHERE media_type = :media "
                "AND provider_id IN (SELECT id FROM custom_provider WHERE api_format = :api_format)"
            ),
            {"ep": endpoint, "media": media_type, "api_format": api_format},
        )
        model_mapped += result.rowcount or 0
    if model_mapped != model_total:
        raise RuntimeError(
            f"custom_provider_model: {model_total - model_mapped} 条记录的 (api_format, media_type) 不在迁移映射中"
        )

    # 5) drop 旧列 + alter NOT NULL
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.alter_column("endpoint", nullable=False)
        batch_op.drop_column("media_type")

    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.alter_column("discovery_format", nullable=False)
        batch_op.drop_column("api_format")


def downgrade() -> None:
    bind = op.get_bind()

    # 1) provider 表：add api_format，按 discovery_format 回填（NewAPI 信息已丢失，统一兜底为 openai）
    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.add_column(sa.Column("api_format", sa.String(length=32), nullable=True))

    bind.execute(sa.text("UPDATE custom_provider SET api_format = 'google' WHERE discovery_format = 'google'"))
    bind.execute(sa.text("UPDATE custom_provider SET api_format = 'openai' WHERE discovery_format != 'google'"))

    # 2) model 表：add media_type，按 endpoint → media_type 反查（每个 endpoint 一条 UPDATE，fail-loud）
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.add_column(sa.Column("media_type", sa.String(length=16), nullable=True))

    model_total = bind.execute(sa.text("SELECT COUNT(*) FROM custom_provider_model")).scalar() or 0
    model_mapped = 0
    for endpoint, (_api_format, media) in _DOWNGRADE_MAP.items():
        result = bind.execute(
            sa.text("UPDATE custom_provider_model SET media_type = :media WHERE endpoint = :ep"),
            {"media": media, "ep": endpoint},
        )
        model_mapped += result.rowcount or 0
    if model_mapped != model_total:
        raise RuntimeError(
            f"custom_provider_model: {model_total - model_mapped} 条记录的 endpoint 不在 downgrade 映射中"
        )

    # 3) drop 新列 + alter NOT NULL
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.alter_column("media_type", nullable=False)
        batch_op.drop_column("endpoint")

    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.alter_column("api_format", nullable=False)
        batch_op.drop_column("discovery_format")
