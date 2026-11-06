"""Alembic 迁移：视频请求端点拆分保持已有模型行为。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.backends.video_backends.vidu import VIDU_BACKENDS

_CUSTOM_MODELS = [
    ("opaque-vidu", "vidu-video", "shengshu/vidu2.0"),
    ("opaque-kling", "kling-video", "kuaishou/kling-v2-6"),
    ("opaque-grok", "grok-video", "xai/grok-imagine-video"),
    ("future", "vidu-video", None),
    ("VIDUQ3-PRO", "vidu-video", None),
    ("viduq3-turbo", "openai-video", None),
    ("alias-opaque", "vidu-video", "acme/custom-name"),
    ("GROK-IMAGINE-VIDEO", "grok-video", None),
    ("viduq3-mix", "vidu-video", "google/veo-3.1-generate-001"),
    ("sync-api", "vidu-video", "test/synced-profile"),
]
_BUILTIN_MODELS = [
    ("vidu", "builtin-opaque", "vidu-video", "shengshu/viduq3"),
    ("kling", "builtin-kling", "kling-video", "kuaishou/kling-v3"),
    ("grok", "builtin-grok", "grok-video", "xai/grok-imagine-video-1.5"),
]


def _seed(conn: sa.Connection) -> None:
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
                "VALUES (:provider_id, :model_id, 1, 1, 'video', 1, :binding, :endpoint, '2026-10-01', '2026-10-01')"
            ),
            {"provider_id": provider_id, "model_id": model_id, "endpoint": endpoint, "binding": binding},
        )


def _endpoints(conn: sa.Connection) -> dict[tuple[str, str], str | None]:
    custom = conn.execute(sa.text("SELECT model_id, endpoint FROM custom_provider_model")).all()
    builtin = conn.execute(
        sa.text("SELECT provider_id, model_id, endpoint FROM builtin_provider_model WHERE user_added")
    ).all()
    return {("custom", m): e for m, e in custom} | {(p, m): e for p, m, e in builtin}


def test_split_request_profiles_preserves_existing_behavior_and_downgrades(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions("*_split_video_request_profiles.py")
    command.upgrade(cfg, parent)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            _seed(conn)
            conn.execute(
                sa.text(
                    "INSERT INTO catalog_user_entry (maker, name, media_type, entry, user_created, created_at, updated_at) "
                    "VALUES ('acme', 'custom-name', 'video', :entry, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"entry": '{"name": "custom-name", "aliases": ["viduq3-pro-fast"]}'},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO catalog_official_sync (id, dataset_version, catalog, created_at, updated_at) "
                    "VALUES (1, '2026-11-04T00:00:00Z', :catalog, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "catalog": json.dumps(
                        {
                            "dataset_version": "2026-11-04T00:00:00Z",
                            "builtin_providers": {},
                            "entries": [
                                {
                                    "maker": "test",
                                    "name": "synced-profile",
                                    "display_name": "Synced",
                                    "media_type": "video",
                                    "aliases": ["viduq2-pro"],
                                    "facts": {},
                                }
                            ],
                        }
                    ).encode()
                },
            )
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
        ("custom", "opaque-vidu"): "vidu-video-2",
        ("custom", "opaque-kling"): "kling-video-pro-audio",
        ("custom", "opaque-grok"): "grok-video-classic",
        ("custom", "future"): "vidu-video-generic",
        ("custom", "VIDUQ3-PRO"): "vidu-video-q3-pro",
        ("custom", "viduq3-turbo"): "openai-video",
        ("custom", "alias-opaque"): "vidu-video-q3-fast",
        ("custom", "GROK-IMAGINE-VIDEO"): "grok-video-classic",
        ("custom", "viduq3-mix"): "vidu-video-generic",
        ("custom", "sync-api"): "vidu-video-q2-pro",
        ("vidu", "builtin-opaque"): "vidu-video-q3-reference",
        ("kling", "builtin-kling"): "kling-video-audio",
        ("grok", "builtin-grok"): "grok-video-mixed",
    }
    # 升级前 vidu2.0 的 img2video 把 12 秒校正为 8 秒，且不发送音轨开关；绑定不透明 ID 同样保持。
    backend = VIDU_BACKENDS[upgraded[("custom", "opaque-vidu")]]
    request = VideoGenerationRequest(
        prompt="test", output_path=Path("out.mp4"), duration_seconds=12, start_image=Path("in.png")
    )
    usage = backend.metered_usage("opaque-vidu", request)
    assert usage.quantities["output_seconds"] == 8
    assert usage.dimensions["audio"] is False
    assert downgraded == before
