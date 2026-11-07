"""Alembic: video-endpoint backfill preserves bindings, user aliases, and other user-selected endpoints."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.backends.video_backends.agnes import AGNES_VIDEO_BACKENDS
from lib.backends.video_backends.dashscope import DASHSCOPE_VIDEO_BACKENDS

#: Custom-provider models: (model_id, endpoint, entry_binding).
_CUSTOM_MODELS = [
    ("opaque-25", "agnes-video", "agnes/agnes-video-2.5"),
    ("opaque-alias", "agnes-video", "mine/relay"),
    ("synced-relay", "agnes-video", None),
    ("agnes-video-v2.0", "agnes-video", None),
    ("proxy/wan-3-video", "dashscope-async-video", None),
    ("opaque-wan", "dashscope-async-video", "alibaba/wan3.0-video"),
    ("wan_2.7_r2v-0715", "dashscope-async-video", None),
    ("proxy/happyhorse-1.1-r2v", "dashscope-async-video", None),
    ("happyhorse-1.1-i2v", "dashscope-async-video", None),
    ("wan-2.7-image-to-video", "dashscope-async-video", None),
    ("proxy-custom", "dashscope-async-video", None),
    ("agnes-video-2.5", "openai-video", None),
    ("veo-3.1", "gemini-video", None),
]
_BUILTIN_MODELS = [
    ("agnes", "builtin-opaque", "agnes-video", "agnes/agnes-video-2.5-flash"),
    ("dashscope", "wan2.7-i2v", "dashscope-async-video", None),
    ("gemini-vertex", "veo-3.1-generate-001", "gemini-video", None),
    ("gemini-aistudio", "veo-3.1-generate-preview", "gemini-video", None),
]


def _seed(conn: sa.Connection) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO custom_provider (id, display_name, discovery_format, base_url, api_key, created_at, updated_at) "
            "VALUES (1, 'P', 'openai', 'https://x', 'k', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )
    import json

    conn.execute(
        sa.text(
            "INSERT INTO catalog_user_entry (maker, name, media_type, entry, user_created, created_at, updated_at) "
            "VALUES ('mine', 'relay', 'video', :entry, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {
            "entry": json.dumps(
                {"maker": "mine", "name": "relay", "media_type": "video", "aliases": ["agnes-video-2.5-flash"]}
            )
        },
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
                    "entries": [
                        {
                            "maker": "agnes",
                            "name": "agnes-video-2.5",
                            "media_type": "video",
                            "aliases": ["synced-relay"],
                        }
                    ],
                }
            ).encode()
        },
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


def test_existing_video_models_keep_their_request_contract_and_downgrade_restores_the_endpoint(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions("*_split_video_request_endpoints.py")
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
        ("custom", "opaque-25"): "agnes-video-2-5",
        ("custom", "opaque-alias"): "agnes-video-2-5",
        ("custom", "synced-relay"): "agnes-video-2-5",
        ("custom", "agnes-video-v2.0"): "agnes-video-2",
        ("custom", "proxy/wan-3-video"): "dashscope-wan3-video",
        ("custom", "opaque-wan"): "dashscope-wan3-video",
        ("custom", "wan_2.7_r2v-0715"): "dashscope-reference-voice-video",
        ("custom", "proxy/happyhorse-1.1-r2v"): "dashscope-reference-video",
        ("custom", "happyhorse-1.1-i2v"): "dashscope-always-on-video",
        ("custom", "wan-2.7-image-to-video"): "dashscope-always-on-video",
        ("custom", "proxy-custom"): "dashscope-video",
        ("custom", "agnes-video-2.5"): "openai-video",
        ("custom", "veo-3.1"): "gemini-video",
        ("agnes", "builtin-opaque"): "agnes-video-2-5",
        ("dashscope", "wan2.7-i2v"): "dashscope-always-on-video",
        ("gemini-vertex", "veo-3.1-generate-001"): "gemini-vertex-video",
        ("gemini-aistudio", "veo-3.1-generate-preview"): "gemini-video",
    }
    request = VideoGenerationRequest(
        prompt="p", output_path=db_path.parent / "out.mp4", duration_seconds=5, generate_audio=False
    )
    assert (
        AGNES_VIDEO_BACKENDS[upgraded[("custom", "opaque-25")]].metered_usage("opaque-25", request).dimensions["audio"]
        is True
    )
    assert (
        DASHSCOPE_VIDEO_BACKENDS[upgraded[("custom", "opaque-wan")]]
        .metered_usage("opaque-wan", request)
        .dimensions["audio"]
        is False
    )
    assert downgraded == before
