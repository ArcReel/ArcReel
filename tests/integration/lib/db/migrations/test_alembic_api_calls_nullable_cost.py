"""Alembic: nullable api_calls costs, unpriced reasons, and cache read/write columns."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_MIGRATION_GLOB = "*_api_calls_nullable_cost_and_cache_columns.py"
_NEW_COLUMNS = {"unpriced_reason", "cache_read_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens"}

_INSERT_CALL = (
    "INSERT INTO api_calls (id, user_id, project_name, call_type, model, status, started_at, "
    "created_at, updated_at, cost_amount, input_tokens) VALUES "
    "(:id, 'default', 'demo', 'text', 'm', 'success', '2026-09-01 00:00:00', "
    "'2026-09-01 00:00:00', '2026-09-01 00:00:00', :cost_amount, :input_tokens)"
)


def _column_info(engine: sa.Engine) -> dict[str, tuple[bool, str | None]]:
    """Column name → (NOT NULL status, server default)."""
    with engine.begin() as connection:
        return {row[1]: (bool(row[3]), row[4]) for row in connection.execute(sa.text("PRAGMA table_info(api_calls)"))}


def _indexes(engine: sa.Engine) -> set[str]:
    with engine.begin() as connection:
        return {row[1] for row in connection.execute(sa.text("PRAGMA index_list('api_calls')"))}


def _costs(engine: sa.Engine) -> dict[int, float | None]:
    with engine.begin() as connection:
        return {row[0]: row[1] for row in connection.execute(sa.text("SELECT id, cost_amount FROM api_calls"))}


def test_upgrade_makes_cost_nullable_and_adds_columns_without_backfill(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
) -> None:
    revision, parent = migration_revisions(_MIGRATION_GLOB)
    config, database = alembic_cfg
    command.upgrade(config, parent)
    engine = sa.create_engine(f"sqlite:///{database}")
    try:
        before = _column_info(engine)
        assert not _NEW_COLUMNS & before.keys()
        assert before["cost_amount"][0] is True
        with engine.begin() as connection:
            connection.execute(sa.text(_INSERT_CALL), {"id": 1, "cost_amount": 0.0, "input_tokens": 10})
            connection.execute(sa.text(_INSERT_CALL), {"id": 2, "cost_amount": 1.5, "input_tokens": None})

        command.upgrade(config, revision)

        after = _column_info(engine)
        assert after.keys() >= _NEW_COLUMNS
        # Costs become nullable with unchanged server defaults; all new columns are nullable without defaults.
        assert after["cost_amount"][0] is False
        assert after["cost_amount"][1] in ("0.0", "'0.0'")
        assert all(after[name] == (False, None) for name in _NEW_COLUMNS)
        # Preserve historical rows: neither turn 0 into NULL nor backfill new columns.
        assert _costs(engine) == {1: 0.0, 2: 1.5}
        with engine.begin() as connection:
            rows = connection.execute(
                sa.text(
                    "SELECT unpriced_reason, cache_read_tokens, cache_write_5m_tokens, cache_write_1h_tokens, "
                    "input_tokens FROM api_calls ORDER BY id"
                )
            ).fetchall()
        assert [tuple(row) for row in rows] == [(None, None, None, None, 10), (None, None, None, None, None)]
        # Table recreation retains existing indexes.
        assert _indexes(engine) >= {"idx_api_calls_project_name", "idx_api_calls_status", "idx_api_calls_task_id"}
        # Migration permits NULL costs distinct from 0.
        with engine.begin() as connection:
            connection.execute(sa.text(_INSERT_CALL), {"id": 3, "cost_amount": None, "input_tokens": 5})
        assert _costs(engine)[3] is None
    finally:
        engine.dispose()


def test_downgrade_restores_not_null_cost_and_drops_new_columns(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
) -> None:
    revision, parent = migration_revisions(_MIGRATION_GLOB)
    config, database = alembic_cfg
    command.upgrade(config, revision)
    engine = sa.create_engine(f"sqlite:///{database}")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text(_INSERT_CALL), {"id": 1, "cost_amount": None, "input_tokens": 5})
            connection.execute(sa.text(_INSERT_CALL), {"id": 2, "cost_amount": 2.5, "input_tokens": 5})

        command.downgrade(config, parent)

        info = _column_info(engine)
        assert not _NEW_COLUMNS & info.keys()
        assert info["cost_amount"][0] is True
        # Downgrade can only turn unpriced costs into 0; priced rows remain unchanged.
        assert _costs(engine) == {1: 0.0, 2: 2.5}
        assert _indexes(engine) >= {"idx_api_calls_project_name", "idx_api_calls_status", "idx_api_calls_task_id"}
    finally:
        engine.dispose()
