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
    ("google", "video"): "newapi-video",  # 兜底；google 直连本无视频
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

    # 1) provider 表：先 add 新列（先不 drop 旧列）
    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.add_column(sa.Column("discovery_format", sa.String(length=32), nullable=True))

    rows = bind.execute(sa.text("SELECT id, api_format FROM custom_provider")).fetchall()
    for row in rows:
        new_val = _UPGRADE_DISCOVERY_MAP.get(row.api_format)
        if new_val is None:
            raise RuntimeError(f"provider id={row.id} api_format={row.api_format!r} 不在映射中")
        bind.execute(
            sa.text("UPDATE custom_provider SET discovery_format = :v WHERE id = :id"),
            {"v": new_val, "id": row.id},
        )

    # 2) model 表：add endpoint，回填（join provider 取 api_format）
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.add_column(sa.Column("endpoint", sa.String(length=32), nullable=True))

    rows = bind.execute(
        sa.text(
            "SELECT m.id AS mid, m.media_type AS media_type, p.api_format AS api_format "
            "FROM custom_provider_model m JOIN custom_provider p ON p.id = m.provider_id"
        )
    ).fetchall()
    for row in rows:
        ep = _UPGRADE_ENDPOINT_MAP.get((row.api_format, row.media_type))
        if ep is None:
            raise RuntimeError(
                f"model id={row.mid} (api_format={row.api_format!r}, media_type={row.media_type!r}) 不在迁移映射中"
            )
        bind.execute(
            sa.text("UPDATE custom_provider_model SET endpoint = :v WHERE id = :id"),
            {"v": ep, "id": row.mid},
        )

    # 3) drop 旧列
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.alter_column("endpoint", nullable=False)
        batch_op.drop_column("media_type")

    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.alter_column("discovery_format", nullable=False)
        batch_op.drop_column("api_format")


def downgrade() -> None:
    bind = op.get_bind()

    # 1) provider 表：add api_format，回填
    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.add_column(sa.Column("api_format", sa.String(length=32), nullable=True))

    rows = bind.execute(sa.text("SELECT id, discovery_format FROM custom_provider")).fetchall()
    for row in rows:
        # discovery_format=openai 反向回 openai（NewAPI 信息已丢失，无法精准还原；以 openai 兜底）
        api_format_val = "google" if row.discovery_format == "google" else "openai"
        bind.execute(
            sa.text("UPDATE custom_provider SET api_format = :v WHERE id = :id"),
            {"v": api_format_val, "id": row.id},
        )

    # 2) model 表：add media_type，回填（按 endpoint 反查）
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.add_column(sa.Column("media_type", sa.String(length=16), nullable=True))

    rows = bind.execute(sa.text("SELECT id, endpoint FROM custom_provider_model")).fetchall()
    for row in rows:
        rev = _DOWNGRADE_MAP.get(row.endpoint)
        if rev is None:
            raise RuntimeError(f"model id={row.id} endpoint={row.endpoint!r} 不在 downgrade 映射中")
        _, media = rev
        bind.execute(
            sa.text("UPDATE custom_provider_model SET media_type = :v WHERE id = :id"),
            {"v": media, "id": row.id},
        )

    # 3) drop 新列 + alter NOT NULL
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.alter_column("media_type", nullable=False)
        batch_op.drop_column("endpoint")

    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.alter_column("api_format", nullable=False)
        batch_op.drop_column("discovery_format")
