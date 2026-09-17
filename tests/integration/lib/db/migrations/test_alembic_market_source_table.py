"""Alembic 迁移：market_source 建表的 upgrade / downgrade。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_MIGRATION = "*_create_market_source_table.py"

_EXPECTED_COLUMNS = {
    "id",
    "kind",
    "display_name",
    "address",
    "index_url",
    "canonical_key",
    "is_enabled",
    "position",
    "cached_index",
    "fetched_at",
    "etag",
    "status",
    "last_error",
    "created_at",
    "updated_at",
}

_INSERT = sa.text(
    "INSERT INTO market_source "
    "(kind, display_name, address, index_url, canonical_key, is_enabled, position, status, created_at, updated_at) "
    "VALUES ('custom', '团队市场', 'someone/market', "
    "'https://raw.githubusercontent.com/someone/market/HEAD/arcreel-market.json', "
    "'github:someone/market@HEAD', 1, 0, 'never_fetched', '2026-09-17 00:00:00', '2026-09-17 00:00:00')"
)


def _columns(engine: sa.Engine) -> set[str]:
    with engine.begin() as conn:
        rows = conn.execute(sa.text("PRAGMA table_info(market_source)")).fetchall()
    return {row[1] for row in rows}


def test_upgrade_creates_table_with_unique_canonical_key(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    revision_id, parent_id = migration_revisions(_MIGRATION)
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, parent_id)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        assert not _columns(engine), "建表前不应存在 market_source"

        command.upgrade(cfg, revision_id)

        assert _columns(engine) == _EXPECTED_COLUMNS
        with engine.begin() as conn:
            conn.execute(_INSERT)
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
            conn.execute(_INSERT)
    finally:
        engine.dispose()


def test_downgrade_drops_table(alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]):
    revision_id, parent_id = migration_revisions(_MIGRATION)
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, revision_id)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        command.downgrade(cfg, parent_id)

        assert not _columns(engine)
    finally:
        engine.dispose()
