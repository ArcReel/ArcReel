"""Alembic 迁移：新建正式素材对象与引用表，降级时删除。"""

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


def test_upgrade_creates_tables_and_downgrade_drops_them(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions("*_object_storage_formal_objects.py")

    new_tables = {"object_storage_formal_object", "object_storage_formal_object_ref"}
    command.upgrade(cfg, revision)
    assert new_tables <= set(_tables(db_path))

    command.downgrade(cfg, parent)
    assert not new_tables & set(_tables(db_path))
