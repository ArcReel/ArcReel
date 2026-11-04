"""已有图片模型的尺寸请求与计量保持不变，用户选过的其他端点不变。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from lib.backends.image_backends.base import ImageRequestFacts
from lib.backends.image_backends.registry import channel_usage_estimator

# 旧图片后端不读条目绑定：不透明 ID 即便绑定旧代际，仍走默认尺寸约定。
_CUSTOM_MODELS = [
    ("ep-opaque", "ark-seedream", "bytedance/doubao-seedream-3-0"),
    ("doubao-seedream-3-0-t2i", "ark-seedream", None),
    ("wan2.7-image-pro", "dashscope-image", None),
    ("qwen-image-edit-plus", "dashscope-image", None),
    ("qwen-image-2.0", "dashscope-image", None),
    ("seedream-3", "openai-images", None),
]
_BUILTIN_MODELS = [
    ("ark", "ep-builtin", "ark-seedream", "bytedance/doubao-seedream-3-0"),
    ("ark-agent-plan", "doubao-seedream-3-0-t2i", "ark-seedream", None),
    ("agnes", "agnes-image-2.5-flash", "agnes-image", None),
    ("agnes", "agnes-image-2.1-flash", "agnes-image", None),
    ("dashscope", "qwen-image-edit-max", "dashscope-image", None),
    ("dashscope", "wan2.7-image", "dashscope-image", None),
    ("ark", "doubao-seedream-3-other", "openai-images", None),
]


def _seed(conn: sa.Connection) -> None:
    conn.execute(sa.text("DELETE FROM builtin_provider_model"))
    conn.execute(
        sa.text(
            "INSERT INTO custom_provider (id, display_name, discovery_format, base_url, api_key, created_at, updated_at) "
            "VALUES (1, 'P', 'openai', 'https://x', 'k', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )
    for index, (model_id, endpoint, binding) in enumerate(_CUSTOM_MODELS, start=1):
        conn.execute(
            sa.text(
                "INSERT INTO custom_provider_model (id, provider_id, model_id, display_name, endpoint, entry_binding, "
                "is_default, is_enabled, created_at, updated_at) VALUES "
                "(:id, 1, :model, :model, :endpoint, :binding, 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": index, "model": model_id, "endpoint": endpoint, "binding": binding},
        )
    for provider_id, model_id, endpoint, binding in _BUILTIN_MODELS:
        conn.execute(
            sa.text(
                "INSERT INTO builtin_provider_model "
                "(provider_id, model_id, enabled, user_added, media_type, multiplier, entry_binding, endpoint, "
                "created_at, updated_at) "
                "VALUES (:provider_id, :model_id, 1, 1, 'image', 1, :binding, :endpoint, '2026-10-01', '2026-10-01')"
            ),
            {"provider_id": provider_id, "model_id": model_id, "endpoint": endpoint, "binding": binding},
        )


def _endpoints(conn: sa.Connection) -> dict[tuple[str, str], str | None]:
    custom = conn.execute(sa.text("SELECT model_id, endpoint FROM custom_provider_model")).all()
    builtin = conn.execute(
        sa.text("SELECT provider_id, model_id, endpoint FROM builtin_provider_model WHERE user_added")
    ).all()
    return {("custom", m): e for m, e in custom} | {(p, m): e for p, m, e in builtin}


def test_existing_image_models_keep_request_and_metering_and_other_endpoints_unchanged(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions("*_split_image_endpoints.py")
    command.upgrade(cfg, parent)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            _seed(conn)
            before = _endpoints(conn)
        command.upgrade(cfg, revision)
        with engine.connect() as conn:
            upgraded = _endpoints(conn)
        command.downgrade(cfg, parent)
        with engine.connect() as conn:
            downgraded = _endpoints(conn)
    finally:
        engine.dispose()

    assert upgraded == {
        ("custom", "ep-opaque"): "ark-seedream-4",
        ("custom", "doubao-seedream-3-0-t2i"): "ark-seedream-3",
        ("custom", "wan2.7-image-pro"): "dashscope-wan-image",
        ("custom", "qwen-image-edit-plus"): "dashscope-qwen-image-edit",
        ("custom", "qwen-image-2.0"): "dashscope-qwen-image",
        ("custom", "seedream-3"): "openai-images",
        ("ark", "ep-builtin"): "ark-seedream-4",
        ("ark-agent-plan", "doubao-seedream-3-0-t2i"): "ark-seedream-3",
        ("agnes", "agnes-image-2.5-flash"): "agnes-image-tier",
        ("agnes", "agnes-image-2.1-flash"): "agnes-image-pixels",
        ("dashscope", "qwen-image-edit-max"): "dashscope-qwen-image-edit",
        ("dashscope", "wan2.7-image"): "dashscope-wan-image",
        ("ark", "doubao-seedream-3-other"): "openai-images",
    }
    # 升级前这三条请求的分辨率计量，与新挂接端点的估算保持一致。
    expected_resolutions = {
        ("custom", "ep-opaque"): "1600x2848",
        ("custom", "doubao-seedream-3-0-t2i"): "720x1280",
        ("agnes", "agnes-image-2.5-flash"): "1K",
        ("agnes", "agnes-image-2.1-flash"): "2K",
    }
    assert {
        key: channel_usage_estimator(str(upgraded[key]))(
            ImageRequestFacts(model=key[1], aspect_ratio="9:16")
        ).dimensions["resolution"]
        for key in expected_resolutions
    } == expected_resolutions
    assert downgraded == before
