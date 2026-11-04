"""Alembic 迁移：内置供应商下用户添加的模型记下调用端点，回填为升级前实际使用的通道。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command


def _upgrade_with(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
    rows: list[dict[str, object]],
) -> dict[tuple[str, str], str | None]:
    """在父版本写入内置供应商模型行，升到本迁移，读回这些行的 ``(provider_id, model_id) → endpoint``。

    更早的迁移会预置官方收录模型的状态行，只读回本用例写入的。
    """
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions("*_builtin_model_endpoint.py")
    command.upgrade(cfg, parent)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            for row in rows:
                conn.execute(
                    sa.text(
                        "INSERT INTO builtin_provider_model "
                        "(provider_id, model_id, enabled, user_added, media_type, multiplier, entry_binding, "
                        "created_at, updated_at) "
                        "VALUES (:provider_id, :model_id, 1, :user_added, :media_type, 1, :entry_binding, "
                        "'2026-10-01', '2026-10-01')"
                    ),
                    {"user_added": True, "media_type": None, "entry_binding": None, **row},
                )
        command.upgrade(cfg, revision)
        with engine.connect() as conn:
            result = conn.execute(sa.text("SELECT provider_id, model_id, endpoint FROM builtin_provider_model")).all()
    finally:
        engine.dispose()
    written = {(str(row["provider_id"]), str(row["model_id"])) for row in rows}
    return {(p, m): endpoint for p, m, endpoint in result if (p, m) in written}


def test_user_added_models_keep_the_channel_they_used_before_upgrade(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    endpoints = _upgrade_with(
        alembic_cfg,
        migration_revisions,
        [
            # 靠条目绑定识别为 H3 的不透明 ID：升级前内置 MiniMax 只按 API 模型名选通道，走的是海螺 2.3 的请求结构
            {
                "provider_id": "minimax",
                "model_id": "acme-video-pro",
                "media_type": "video",
                "entry_binding": "minimax/MiniMax-H3",
            },
            {"provider_id": "minimax", "model_id": "proxy/minimax-h3-preview", "media_type": "video"},
            {"provider_id": "minimax", "model_id": "MiniMax-Hailuo-02-Fast", "media_type": "video"},
            {"provider_id": "ark", "model_id": "doubao-seedance-x", "media_type": "video"},
            {"provider_id": "grok", "model_id": "grok-imagine-image-2", "media_type": "image"},
            {"provider_id": "openai", "model_id": "gpt-5.9", "media_type": "text"},
            # 官方收录模型的启用状态行：端点跟随官方，不记
            {"provider_id": "minimax", "model_id": "MiniMax-H3", "user_added": False},
        ],
    )

    assert endpoints == {
        ("minimax", "acme-video-pro"): "minimax-hailuo-v1",
        ("minimax", "proxy/minimax-h3-preview"): "minimax-h3",
        ("minimax", "MiniMax-Hailuo-02-Fast"): "minimax-hailuo-v1-fast",
        ("ark", "doubao-seedance-x"): "ark-seedance",
        ("grok", "grok-imagine-image-2"): "grok-image",
        ("openai", "gpt-5.9"): "openai-responses",
        ("minimax", "MiniMax-H3"): None,
    }


def test_downgrade_drops_the_endpoint_column(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions("*_builtin_model_endpoint.py")
    command.upgrade(cfg, revision)
    command.downgrade(cfg, parent)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            columns = {row[1] for row in conn.execute(sa.text("PRAGMA table_info(builtin_provider_model)"))}
    finally:
        engine.dispose()
    assert "endpoint" not in columns
