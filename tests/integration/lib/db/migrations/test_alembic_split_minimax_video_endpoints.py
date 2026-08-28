from __future__ import annotations

import hashlib
import json
from pathlib import Path

import sqlalchemy as sa

from alembic import command
from lib.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint

REVISION = "8c2b1e7d4a90"
DOWN_REVISION = "3b7c921d5e44"


def _digest(values: dict[str, object]) -> str:
    payload = {key: value for key, value in values.items() if key not in {"api_call_id", "request_digest"}}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _checkpoint(task_id: str, model: str, endpoint: str) -> str:
    prompt = "frozen"
    values: dict[str, object] = {
        "schema_version": 1,
        "kind": "reference_video_submit",
        "task_id": task_id,
        "project_name": "demo",
        "script_file": "scripts/episode_1.json",
        "unit_id": "E1U1",
        "capability": "r2v",
        "provider_id": "custom-1",
        "provider_model_id": model,
        "backend_model_id": model,
        "endpoint_guard": endpoint,
        "api_call_id": 7,
        "prompt": prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "duration_seconds": 6,
        "aspect_ratio": "16:9",
        "resolution": "768p",
        "generate_audio": False,
        "service_tier": "default",
        "seed": None,
        "visual_basis_digest": "a" * 64,
        "narration": {
            "delivery": "post_production",
            "tts_status": "not_applicable",
            "artifact_path": "",
            "basis_digest": None,
            "actual_duration_seconds": None,
        },
        "media": [],
        "reference_audio_targets": None,
    }
    values["request_digest"] = _digest(values)
    return json.dumps(values)


def _seed(engine: sa.Engine) -> None:
    models = [
        ("MiniMax-Hailuo-2.3", "minimax-video"),
        ("MiniMax-Hailuo-2.3-Fast", "minimax-video"),
        ("S2V-01", "minimax-video"),
        ("MiniMax-H3", "minimax-video"),
        ("other", "openai-video"),
        # 带命名空间前缀的 Fast 别名归通用海螺键：Fast 档只认精确型号名。
        ("proxy/MiniMax-Hailuo-2.3-Fast", "minimax-video"),
    ]
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO custom_provider (id, display_name, discovery_format, base_url, api_key, created_at, updated_at) "
                "VALUES (1, 'P', 'openai', 'https://x', 'k', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        for index, (model, endpoint) in enumerate(models, start=1):
            connection.execute(
                sa.text(
                    "INSERT INTO custom_provider_model (id, provider_id, model_id, display_name, endpoint, "
                    "is_default, is_enabled, created_at, updated_at) VALUES "
                    "(:id, 1, :model, :model, :endpoint, 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": index, "model": model, "endpoint": endpoint},
            )
            task_id = f"T-{index}"
            connection.execute(
                sa.text(
                    "INSERT INTO tasks (task_id, project_name, task_type, media_type, resource_id, status, "
                    "source, execution_checkpoint_json, queued_at, updated_at) VALUES "
                    "(:task_id, 'demo', 'reference_video', 'video', :task_id, 'running', 'webui', "
                    ":checkpoint, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"task_id": task_id, "checkpoint": _checkpoint(task_id, model, endpoint)},
            )


def _read(engine: sa.Engine, table: str, field: str) -> dict[str, str]:
    identity = "model_id" if table == "custom_provider_model" else "task_id"
    with engine.connect() as connection:
        rows = connection.execute(sa.text(f"SELECT {identity}, {field} FROM {table}"))
        return {row[0]: row[1] for row in rows}


def test_upgrade_and_downgrade_rewrite_models_and_resumable_checkpoints(alembic_cfg) -> None:
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, DOWN_REVISION)
    engine = sa.create_engine(f"sqlite:///{Path(db_path)}")
    _seed(engine)
    # 坏掉的 checkpoint 是单条任务的身份问题，不该让整个迁移中止；该行原样保留。
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO tasks (task_id, project_name, task_type, media_type, resource_id, status, "
                "source, execution_checkpoint_json, queued_at, updated_at) VALUES "
                "('T-broken', 'demo', 'reference_video', 'video', 'T-broken', 'running', 'webui', "
                "'{not json', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    command.upgrade(cfg, REVISION)

    assert _read(engine, "custom_provider_model", "endpoint") == {
        "MiniMax-Hailuo-2.3": "minimax-hailuo-v1",
        "MiniMax-Hailuo-2.3-Fast": "minimax-hailuo-v1-fast",
        "S2V-01": "minimax-s2v-01",
        "MiniMax-H3": "minimax-h3",
        "other": "openai-video",
        "proxy/MiniMax-Hailuo-2.3-Fast": "minimax-hailuo-v1",
    }
    upgraded = _read(engine, "tasks", "execution_checkpoint_json")
    for task_id, endpoint in {
        "T-1": "minimax-hailuo-v1",
        "T-2": "minimax-hailuo-v1-fast",
        "T-3": "minimax-s2v-01",
        "T-4": "minimax-h3",
        "T-6": "minimax-hailuo-v1",
    }.items():
        assert ReferenceSubmissionCheckpoint.from_json(upgraded[task_id]).endpoint_guard == endpoint
    assert ReferenceSubmissionCheckpoint.from_json(upgraded["T-5"]).endpoint_guard == "openai-video"
    assert upgraded["T-broken"] == "{not json"

    command.downgrade(cfg, DOWN_REVISION)

    downgraded = _read(engine, "tasks", "execution_checkpoint_json")
    for task_id in ("T-1", "T-2", "T-3", "T-4", "T-6"):
        assert ReferenceSubmissionCheckpoint.from_json(downgraded[task_id]).endpoint_guard == "minimax-video"
    engine.dispose()
