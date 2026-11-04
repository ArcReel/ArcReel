"""Alembic 迁移：方舟 Seedance 端点按代际拆成三个，已有模型回填为升级前实际使用的那一代。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from lib.backends.video_backends.ark import SEEDANCE_BACKENDS

#: 自定义供应商模型：(model_id, endpoint, entry_binding)
_CUSTOM_MODELS = [
    # 靠条目绑定识别为 Seedance 2.0 的不透明 ID：升级前按条目的名字不带 service_tier、按 2.0 像素表估算
    ("ep-20261103-opaque", "ark-seedance", "bytedance/doubao-seedance-2.0"),
    ("ep-20261103-unbound", "ark-seedance", None),
    ("opaque-alias", "ark-seedance", None),
    ("bound-alias", "ark-seedance", "acme/opaque-entry"),
    ("SYNC-ALIAS", "ark-seedance", "missing/entry"),
    ("dreamina-seedance-2-5-260628", "ark-seedance", None),
    ("doubao-seedance-2.0-fast", "ark-seedance", None),
    ("doubao-seedance-1-5-pro-251215", "ark-seedance", None),
    # 用户选过的、没有拆分的端点保持不变
    ("doubao-seedance-2-0-260128", "openai-video", None),
]

#: 内置供应商下用户添加的模型：(provider_id, model_id, endpoint, entry_binding)
_BUILTIN_MODELS = [
    ("ark", "ep-builtin-opaque", "ark-seedance", "bytedance/doubao-seedance-2.5"),
    ("ark-agent-plan", "doubao-seedance-1.5-pro", "ark-seedance", None),
    ("ark", "opaque-alias", "ark-seedance", None),
    ("minimax", "acme-video-pro", "minimax-hailuo-v1", "minimax/MiniMax-H3"),
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


def test_existing_seedance_models_keep_the_generation_they_used_and_downgrade_restores_the_old_endpoint(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions("*_split_seedance_endpoints.py")
    command.upgrade(cfg, parent)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            _seed(conn)
            for maker, name, aliases in (
                ("bytedance", "doubao-seedance-2.0", ["opaque-alias"]),
                ("acme", "opaque-entry", ["doubao-seedance-2.5"]),
            ):
                conn.execute(
                    sa.text(
                        "INSERT INTO catalog_user_entry "
                        "(maker, name, media_type, entry, user_created, created_at, updated_at) "
                        "VALUES (:maker, :name, 'video', :entry, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {"maker": maker, "name": name, "entry": json.dumps({"name": name, "aliases": aliases})},
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
                                    "maker": "bytedance",
                                    "name": "doubao-seedance-2.5",
                                    "media_type": "video",
                                    "aliases": ["sync-alias"],
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
        ("custom", "ep-20261103-opaque"): "ark-seedance-2",
        ("custom", "ep-20261103-unbound"): "ark-seedance-1",
        ("custom", "opaque-alias"): "ark-seedance-2",
        ("custom", "bound-alias"): "ark-seedance-2-5",
        ("custom", "SYNC-ALIAS"): "ark-seedance-2-5",
        ("ark", "opaque-alias"): "ark-seedance-2",
        ("custom", "dreamina-seedance-2-5-260628"): "ark-seedance-2-5",
        ("custom", "doubao-seedance-2.0-fast"): "ark-seedance-2",
        ("custom", "doubao-seedance-1-5-pro-251215"): "ark-seedance-1",
        ("custom", "doubao-seedance-2-0-260128"): "openai-video",
        ("ark", "ep-builtin-opaque"): "ark-seedance-2-5",
        ("ark-agent-plan", "doubao-seedance-1.5-pro"): "ark-seedance-1",
        ("minimax", "acme-video-pro"): "minimax-hailuo-v1",
    }
    # 请求与升级前一致：升级前只要模型或所绑定条目的名字属于 2.x 就不带 service_tier
    sent_service_tier_before_upgrade = {
        ("custom", "ep-20261103-opaque"): False,
        ("custom", "ep-20261103-unbound"): True,
        ("custom", "opaque-alias"): False,
        ("custom", "bound-alias"): False,
        ("custom", "SYNC-ALIAS"): False,
        ("ark", "opaque-alias"): False,
        ("custom", "dreamina-seedance-2-5-260628"): False,
        ("custom", "doubao-seedance-2.0-fast"): False,
        ("custom", "doubao-seedance-1-5-pro-251215"): True,
        ("ark", "ep-builtin-opaque"): False,
        ("ark-agent-plan", "doubao-seedance-1.5-pro"): True,
    }
    assert {
        key: SEEDANCE_BACKENDS[str(upgraded[key])].endpoint.sends_service_tier
        for key in sent_service_tier_before_upgrade
    } == sent_service_tier_before_upgrade
    assert (
        SEEDANCE_BACKENDS[upgraded[("custom", "opaque-alias")]].estimate_video_tokens(
            resolution="720p", ratio="16:9", seconds=5
        )
        == 108000.0
    )
    assert downgraded == before
