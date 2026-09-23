"""Alembic api_calls.generate_audio 去默认迁移的双向测试。"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from alembic import command

_PREVIOUS_REVISION = "23c60be146cc"
_REVISION = "b66f6d617e3f"

_INSERT = (
    "INSERT INTO api_calls (project_name, call_type, model, started_at, created_at{cols}) "
    "VALUES ('demo', '{call_type}', 'm', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP{vals})"
)


def _sync_engine(db_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{db_path}")


def _generate_audio_by_call_type(engine: sa.Engine) -> dict[str, object]:
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT call_type, generate_audio FROM api_calls ORDER BY id")).fetchall()
    return {row.call_type: row.generate_audio for row in rows}


def test_upgrade_clears_non_video_rows_and_stops_defaulting_new_ones(alembic_cfg):
    """前置：旧默认把未声明的图像行填成 1；迁移后历史图像行清空、视频行原样、新插入未声明即 NULL。"""
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, _PREVIOUS_REVISION)

    engine = _sync_engine(db_path)
    with engine.begin() as conn:
        conn.execute(sa.text(_INSERT.format(call_type="image", cols="", vals="")))
        conn.execute(sa.text(_INSERT.format(call_type="video", cols=", generate_audio", vals=", 0")))
    assert _generate_audio_by_call_type(engine) == {"image": 1, "video": 0}

    command.upgrade(cfg, _REVISION)

    assert _generate_audio_by_call_type(engine) == {"image": None, "video": 0}
    with engine.begin() as conn:
        conn.execute(sa.text(_INSERT.format(call_type="text", cols="", vals="")))
    assert _generate_audio_by_call_type(engine)["text"] is None


def test_downgrade_restores_the_default_without_touching_rows(alembic_cfg):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, _REVISION)

    engine = _sync_engine(db_path)
    with engine.begin() as conn:
        conn.execute(sa.text(_INSERT.format(call_type="image", cols="", vals="")))

    command.downgrade(cfg, _PREVIOUS_REVISION)

    with engine.begin() as conn:
        conn.execute(sa.text(_INSERT.format(call_type="text", cols="", vals="")))
    assert _generate_audio_by_call_type(engine) == {"image": None, "text": 1}
