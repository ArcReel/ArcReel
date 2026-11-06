"""Alembic 迁移：新建对象存储连接、素材库账号与绑定表，降级时删除。"""

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
    revision, parent = migration_revisions("*_object_storage_and_asset_library.py")

    new_tables = {"object_storage_connection", "asset_library_account", "asset_library_binding"}
    command.upgrade(cfg, revision)
    assert new_tables <= set(_tables(db_path))

    command.downgrade(cfg, parent)
    assert not new_tables & set(_tables(db_path))
