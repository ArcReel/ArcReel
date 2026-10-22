"""Alembic 迁移：新建官方同步表，降级时删除。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command


def _tables(db_path: Path) -> list[str]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        return sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_upgrade_creates_table_and_downgrade_drops_it(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions("*_catalog_official_sync.py")

    command.upgrade(cfg, revision)
    assert "catalog_official_sync" in _tables(db_path)

    command.downgrade(cfg, parent)
    assert "catalog_official_sync" not in _tables(db_path)
