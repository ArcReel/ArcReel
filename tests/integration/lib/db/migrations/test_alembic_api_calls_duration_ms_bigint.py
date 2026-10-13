"""Alembic api_calls.duration_ms 扩为 64 位整数迁移的双向测试。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa

from alembic import command

_MIGRATION_GLOB = "*_api_calls_duration_ms_bigint.py"

_THIRTY_DAYS_MS = 30 * 24 * 3600 * 1000

_INSERT = (
    "INSERT INTO api_calls (project_name, call_type, model, started_at, created_at, duration_ms) "
    "VALUES ('demo', '{call_type}', 'm', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, {duration_ms})"
)


def _sync_engine(db_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{db_path}")


def _duration_type(engine: sa.Engine) -> str:
    columns = sa.inspect(engine).get_columns("api_calls")
    return str(next(c["type"] for c in columns if c["name"] == "duration_ms")).upper()


def _api_call_indexes(engine: sa.Engine) -> set[str | None]:
    return {ix["name"] for ix in sa.inspect(engine).get_indexes("api_calls")}


def _durations(engine: sa.Engine) -> dict[str, int | None]:
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT call_type, duration_ms FROM api_calls ORDER BY id")).fetchall()
    return {row.call_type: row.duration_ms for row in rows}


def test_upgrade_widens_column_and_keeps_rows_and_indexes(
    alembic_cfg, migration_revisions: Callable[[str], tuple[str, str]]
):
    revision, parent = migration_revisions(_MIGRATION_GLOB)
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, parent)

    engine = _sync_engine(db_path)
    assert _duration_type(engine) == "INTEGER"
    with engine.begin() as conn:
        conn.execute(sa.text(_INSERT.format(call_type="image", duration_ms=1200)))
        conn.execute(sa.text(_INSERT.format(call_type="text", duration_ms="NULL")))
    indexes_before = _api_call_indexes(engine)

    command.upgrade(cfg, revision)

    assert _duration_type(engine) == "BIGINT"
    assert _durations(engine) == {"image": 1200, "text": None}
    assert _api_call_indexes(engine) == indexes_before
    with engine.begin() as conn:
        conn.execute(sa.text(_INSERT.format(call_type="video", duration_ms=_THIRTY_DAYS_MS)))
    assert _durations(engine)["video"] == _THIRTY_DAYS_MS


def test_downgrade_narrows_column_and_nulls_values_outside_int32(
    alembic_cfg, migration_revisions: Callable[[str], tuple[str, str]]
):
    revision, parent = migration_revisions(_MIGRATION_GLOB)
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, revision)

    engine = _sync_engine(db_path)
    with engine.begin() as conn:
        conn.execute(sa.text(_INSERT.format(call_type="image", duration_ms=1200)))
        conn.execute(sa.text(_INSERT.format(call_type="video", duration_ms=_THIRTY_DAYS_MS)))
        conn.execute(sa.text(_INSERT.format(call_type="text", duration_ms=-_THIRTY_DAYS_MS)))
    indexes_before = _api_call_indexes(engine)

    command.downgrade(cfg, parent)

    assert _duration_type(engine) == "INTEGER"
    assert _durations(engine) == {"image": 1200, "video": None, "text": None}
    assert _api_call_indexes(engine) == indexes_before
