"""Alembic 迁移：api_calls 的使用记录列（加列 + 从任务载荷反查 task_id 的回填）。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_MIGRATION_GLOB = "*_add_usage_record_columns_to_api_calls.py"
_NEW_COLUMNS = {"task_id", "purpose", "session_id", "inputs", "error_code", "error_params"}

_INSERT_TASK = (
    "INSERT INTO tasks (task_id, project_name, task_type, media_type, resource_id, status, source, "
    "payload_json, queued_at, updated_at) VALUES "
    "(:task_id, 'demo', :task_type, :media_type, 'E1S01', 'succeeded', 'webui', "
    ":payload_json, '2026-09-01 00:00:00', '2026-09-01 00:00:00')"
)
_INSERT_CALL = (
    "INSERT INTO api_calls (id, user_id, project_name, call_type, model, status, started_at, "
    "created_at, updated_at) VALUES "
    "(:id, 'default', 'demo', :call_type, 'veo-3.1-generate-preview', 'success', "
    "'2026-09-01 00:00:00', '2026-09-01 00:00:00', '2026-09-01 00:00:00')"
)


def _columns(engine: sa.Engine) -> set[str]:
    with engine.begin() as connection:
        return {row[1] for row in connection.execute(sa.text("PRAGMA table_info(api_calls)"))}


def _indexes(engine: sa.Engine) -> set[str]:
    with engine.begin() as connection:
        return {row[1] for row in connection.execute(sa.text("PRAGMA index_list('api_calls')"))}


def _seed(engine: sa.Engine) -> None:
    """三条调用行：video 任务反查得到、非 video 任务不反查、任务载荷无 api_call_id。"""
    with engine.begin() as connection:
        for call_id, call_type in ((1, "video"), (2, "image"), (3, "video")):
            connection.execute(sa.text(_INSERT_CALL), {"id": call_id, "call_type": call_type})
        connection.execute(
            sa.text(_INSERT_TASK),
            {
                "task_id": "T-video",
                "task_type": "video",
                "media_type": "video",
                "payload_json": '{"api_call_id": 1, "duration": 8}',
            },
        )
        connection.execute(
            sa.text(_INSERT_TASK),
            {
                "task_id": "T-image",
                "task_type": "image",
                "media_type": "image",
                "payload_json": '{"api_call_id": 2}',
            },
        )
        connection.execute(
            sa.text(_INSERT_TASK),
            {
                "task_id": "T-legacy",
                "task_type": "video",
                "media_type": "video",
                "payload_json": '{"prompt": "p"}',
            },
        )


def test_upgrade_adds_columns_and_backfills_video_task_id(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
) -> None:
    revision, parent = migration_revisions(_MIGRATION_GLOB)
    config, database = alembic_cfg
    command.upgrade(config, parent)
    engine = sa.create_engine(f"sqlite:///{database}")
    try:
        assert not _NEW_COLUMNS & _columns(engine)
        _seed(engine)

        command.upgrade(config, revision)

        assert _columns(engine) >= _NEW_COLUMNS
        assert "idx_api_calls_task_id" in _indexes(engine)
        with engine.begin() as connection:
            rows = connection.execute(
                sa.text(
                    "SELECT id, task_id, purpose, session_id, inputs, error_code, error_params "
                    "FROM api_calls ORDER BY id"
                )
            ).fetchall()
        # video 任务反查到的那条带 task_id 与 generation_task；image 任务与无 api_call_id 的
        # 任务都不回填；其余新列一律留空。
        assert [(row[0], row[1], row[2]) for row in rows] == [
            (1, "T-video", "generation_task"),
            (2, None, None),
            (3, None, None),
        ]
        assert all(row[3:] == (None, None, None, None) for row in rows)
    finally:
        engine.dispose()


def test_downgrade_drops_usage_record_columns(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
) -> None:
    revision, parent = migration_revisions(_MIGRATION_GLOB)
    config, database = alembic_cfg
    command.upgrade(config, revision)
    engine = sa.create_engine(f"sqlite:///{database}")
    try:
        command.downgrade(config, parent)

        assert not _NEW_COLUMNS & _columns(engine)
        assert "idx_api_calls_task_id" not in _indexes(engine)
        # 同表既有索引不因逐列 DROP 丢失
        assert _indexes(engine) >= {"idx_api_calls_project_name", "idx_api_calls_status"}
    finally:
        engine.dispose()
