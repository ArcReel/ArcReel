"""Alembic 迁移：能力覆盖扩到两类维度时，已有覆盖按维度原样保留，ComfyUI 端点上的清除。"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_REVISION = "a98bcf8749b2"
_PARENT = "c5f2a8d61e93"
_TS = "'2026-10-15 00:00:00'"


def _insert_provider(conn: sa.Connection, provider_id: int, discovery_format: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO custom_provider (id, display_name, discovery_format, base_url, api_key, created_at, updated_at) "
            f"VALUES (:id, 'P', :fmt, 'https://x', 'k', {_TS}, {_TS})"
        ),
        {"id": provider_id, "fmt": discovery_format},
    )


def _insert_endpoint(conn: sa.Connection, endpoint_id: int, kind: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO custom_endpoint "
            "(id, definition, kind, schema_version, media_type, display_name, created_at, updated_at) "
            f"VALUES (:id, :definition, :kind, '1', 'video', 'E', {_TS}, {_TS})"
        ),
        {"id": endpoint_id, "definition": json.dumps({"kind": kind}), "kind": kind},
    )


def _insert_model(conn: sa.Connection, row_id: int, provider_id: int, endpoint: str, overrides: dict | None) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO custom_provider_model "
            "(id, provider_id, model_id, display_name, endpoint, is_default, is_enabled, capability_overrides, "
            "created_at, updated_at) "
            f"VALUES (:id, :pid, :mid, 'M', :endpoint, 0, 1, :overrides, {_TS}, {_TS})"
        ),
        {
            "id": row_id,
            "pid": provider_id,
            "mid": f"model-{row_id}",
            "endpoint": endpoint,
            "overrides": None if overrides is None else json.dumps(overrides),
        },
    )


def _overrides(conn: sa.Connection) -> dict[int, object]:
    rows = conn.execute(sa.text("SELECT id, capability_overrides FROM custom_provider_model ORDER BY id")).fetchall()
    return {row_id: None if raw is None else json.loads(raw) for row_id, raw in rows}


def test_existing_overrides_kept_by_dimension_and_cleared_on_comfyui(alembic_cfg: tuple[Config, Path]):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, _PARENT)

    channel_overrides = {"last_frame": True, "reference_audio_mode": "direct", "max_reference_audio_count": 2}
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            _insert_provider(conn, 1, "openai")
            _insert_provider(conn, 2, "comfyui")
            _insert_endpoint(conn, 7, "comfyui")
            _insert_endpoint(conn, 8, "declarative")
            # 普通供应商上的视频覆盖：每个键就是一个通道事实维度，原样保留
            _insert_model(conn, 1, 1, "openai-video", channel_overrides)
            # 声明式自定义端点上的覆盖同样保留
            _insert_model(conn, 2, 1, "ce-8", {"max_reference_images": 3})
            # ComfyUI 端点（按端点种类认）上的覆盖清除
            _insert_model(conn, 3, 1, "ce-7", {"last_frame": True})
            # ComfyUI 协议供应商下的覆盖清除
            _insert_model(conn, 4, 2, "ce-7", {"max_reference_images": 1})
            # 没有覆盖的行不受影响
            _insert_model(conn, 5, 1, "openai-video", None)

        command.upgrade(cfg, _REVISION)

        with engine.begin() as conn:
            assert _overrides(conn) == {
                1: channel_overrides,
                2: {"max_reference_images": 3},
                3: None,
                4: None,
                5: None,
            }

        command.downgrade(cfg, _PARENT)
        with engine.begin() as conn:
            assert _overrides(conn)[1] == channel_overrides
    finally:
        engine.dispose()
