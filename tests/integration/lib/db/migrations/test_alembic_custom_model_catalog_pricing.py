"""Alembic 迁移：自定义供应商模型迁入模型目录（docs/adr/0106）。

旧价格列丢弃；可选时长与最大输出 token 等于所对应条目的值或按名字推断的预设时丢弃，其余转为能力覆盖；已有覆盖、
默认分辨率与调用端点原样保留；新增供应商模型倍率（默认 1）与条目绑定。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from tests.factories import comfyui_endpoint_definition

_TS = "'2026-10-15 00:00:00'"


def _comfyui_with_native_five_seconds() -> dict[str, Any]:
    """帧数可驱动的 ComfyUI 视频定义：``length`` 字面 81、``fps`` 读出 16 → 原生 5 秒。"""
    definition = comfyui_endpoint_definition()
    definition["workflow"]["5"]["inputs"]["length"] = 81
    definition["bindings"]["frames"] = [{"node": "5", "input": "length", "class_type": "EmptyLatentImage"}]
    return definition


def _insert_provider(conn: sa.Connection, provider_id: int, discovery_format: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO custom_provider (id, display_name, discovery_format, base_url, api_key, created_at, updated_at) "
            f"VALUES (:id, 'P', :fmt, 'https://x', 'k', {_TS}, {_TS})"
        ),
        {"id": provider_id, "fmt": discovery_format},
    )


def _insert_comfyui_endpoint(conn: sa.Connection, endpoint_id: int, definition: dict[str, Any]) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO custom_endpoint "
            "(id, definition, kind, schema_version, media_type, display_name, created_at, updated_at) "
            f"VALUES (:id, :definition, 'comfyui', '1', 'video', 'E', {_TS}, {_TS})"
        ),
        {"id": endpoint_id, "definition": json.dumps(definition)},
    )


def _insert_model(
    conn: sa.Connection,
    row_id: int,
    *,
    provider_id: int = 1,
    model_id: str,
    endpoint: str,
    durations: list[int] | None = None,
    max_output_tokens: int | None = None,
    overrides: dict[str, object] | None = None,
    resolution: str | None = None,
    price: float | None = None,
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO custom_provider_model "
            "(id, provider_id, model_id, display_name, endpoint, is_default, is_enabled, price_unit, price_input, "
            "price_output, currency, supported_durations, resolution, capability_overrides, max_output_tokens, "
            "created_at, updated_at) "
            "VALUES (:id, :pid, :mid, 'M', :endpoint, 0, 1, 'token', :price, :price, 'CNY', :durations, "
            f":resolution, :overrides, :max_tokens, {_TS}, {_TS})"
        ),
        {
            "id": row_id,
            "pid": provider_id,
            "mid": model_id,
            "endpoint": endpoint,
            "price": price,
            "durations": None if durations is None else json.dumps(durations),
            "resolution": resolution,
            "overrides": None if overrides is None else json.dumps(overrides),
            "max_tokens": max_output_tokens,
        },
    )


def _rows(conn: sa.Connection) -> dict[int, dict[str, object]]:
    rows = conn.execute(
        sa.text(
            "SELECT id, endpoint, resolution, capability_overrides, multiplier, entry_binding "
            "FROM custom_provider_model ORDER BY id"
        )
    ).fetchall()
    return {
        row.id: {
            "endpoint": row.endpoint,
            "resolution": row.resolution,
            "overrides": None if row.capability_overrides is None else json.loads(row.capability_overrides),
            "multiplier": row.multiplier,
            "entry_binding": row.entry_binding,
        }
        for row in rows
    }


def _columns(conn: sa.Connection) -> set[str]:
    return {row[1] for row in conn.execute(sa.text("PRAGMA table_info(custom_provider_model)")).fetchall()}


def test_values_equal_to_entry_or_preset_are_dropped_and_the_rest_become_overrides(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    revision, parent = migration_revisions("c7e2a9d4b6f1_*.py")
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, parent)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            _insert_provider(conn, 1, "openai")
            _insert_provider(conn, 2, "comfyui")
            _insert_comfyui_endpoint(conn, 7, _comfyui_with_native_five_seconds())
            _insert_comfyui_endpoint(conn, 8, comfyui_endpoint_definition())
            # 视频：等于对应条目的值（也等于名字预设）→ 丢弃；价格一并丢弃，默认分辨率保留
            _insert_model(
                conn,
                1,
                model_id="veo-3.1-generate-preview",
                endpoint="openai-video",
                durations=[8, 6, 4],
                resolution="1080p",
                price=0.4,
            )
            # 视频：对应不上条目，等于当前名字预设（一个族都匹配不上时的 [4, 8]）→ 丢弃
            _insert_model(conn, 2, model_id="relay-video-x", endpoint="openai-video", durations=[4, 8])
            # 视频：等于早年回填迁移按当时预设写入的值 → 丢弃
            _insert_model(conn, 3, model_id="relay-minimax-x1", endpoint="openai-video", durations=[6])
            # 视频：对应上条目，与条目和预设都不同 → 转为能力覆盖
            _insert_model(conn, 4, model_id="kling-v2-6", endpoint="openai-video", durations=[5])
            # 视频：对应不上条目，与预设不同 → 转为能力覆盖
            _insert_model(conn, 5, model_id="mystery-video", endpoint="openai-video", durations=[7, 3])
            # 已有的同名覆盖优先，其余覆盖原样保留
            _insert_model(
                conn,
                6,
                model_id="kling-v2-5-turbo",
                endpoint="kling-video",
                durations=[5],
                overrides={"durations": [10], "last_frame": True},
            )
            # 文本：等于条目的最大输出 token → 丢弃
            _insert_model(conn, 7, model_id="gpt-5.4", endpoint="openai-chat", max_output_tokens=128000)
            # 文本：与条目不同 → 转为能力覆盖，其余覆盖保留
            _insert_model(
                conn,
                8,
                model_id="gemini-3-flash-preview",
                endpoint="openai-chat",
                max_output_tokens=8192,
                overrides={"image_input": False},
            )
            # 文本：对应不上条目、没有可比的值 → 转为能力覆盖
            _insert_model(conn, 9, model_id="my-private-llm", endpoint="openai-chat", max_output_tokens=4096)
            # ComfyUI：等于 workflow 原生时长 → 丢弃；增删过 → 转为 durations 覆盖
            _insert_model(conn, 10, provider_id=2, model_id="wf-a", endpoint="ce-7", durations=[5])
            _insert_model(conn, 11, provider_id=2, model_id="wf-b", endpoint="ce-7", durations=[5, 10])
            # ComfyUI：端点驱动不了时长（钉为空集）→ 丢弃
            _insert_model(conn, 12, provider_id=2, model_id="wf-c", endpoint="ce-8", durations=[])
            # 不属于该媒体类型的旧值 → 丢弃
            _insert_model(
                conn, 13, model_id="gpt-image-2", endpoint="openai-images", durations=[4], max_output_tokens=10
            )

        command.upgrade(cfg, revision)

        with engine.begin() as conn:
            columns = _columns(conn)
            rows = _rows(conn)

        assert {
            "price_input",
            "price_output",
            "currency",
            "price_unit",
            "supported_durations",
            "max_output_tokens",
        }.isdisjoint(columns)
        assert {row_id: row["overrides"] for row_id, row in rows.items()} == {
            1: None,
            2: None,
            3: None,
            4: {"durations": [5]},
            5: {"durations": [3, 7]},
            6: {"durations": [10], "last_frame": True},
            7: None,
            8: {"image_input": False, "max_output_tokens": 8192},
            9: {"max_output_tokens": 4096},
            10: None,
            11: {"durations": [5, 10]},
            12: None,
            13: None,
        }
        assert rows[1]["resolution"] == "1080p"
        assert rows[1]["endpoint"] == "openai-video"
        assert rows[10]["endpoint"] == "ce-7"
        assert {row["multiplier"] for row in rows.values()} == {1.0}
        assert {row["entry_binding"] for row in rows.values()} == {None}

        command.downgrade(cfg, parent)
        with engine.begin() as conn:
            columns = _columns(conn)
            overrides = conn.execute(
                sa.text("SELECT capability_overrides FROM custom_provider_model WHERE id = 4")
            ).scalar()
        assert {"price_input", "supported_durations", "max_output_tokens"} <= columns
        assert {"multiplier", "entry_binding"}.isdisjoint(columns)
        assert json.loads(overrides) == {"durations": [5]}
    finally:
        engine.dispose()
