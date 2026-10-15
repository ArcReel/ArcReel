"""Alembic 迁移：新建用户条目表，降级时删除。"""

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
    revision, parent = migration_revisions("*_catalog_user_entry.py")

    command.upgrade(cfg, revision)
    assert "catalog_user_entry" in _tables(db_path)

    command.downgrade(cfg, parent)
    assert "catalog_user_entry" not in _tables(db_path)


def _columns(db_path: Path) -> list[str]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        return [column["name"] for column in sa.inspect(engine).get_columns("catalog_user_entry")]
    finally:
        engine.dispose()


def test_user_created_column_defaults_to_false_for_existing_copies(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions("*_catalog_user_entry_created.py")

    command.upgrade(cfg, parent)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO catalog_user_entry (maker, name, media_type, entry, created_at, updated_at) "
                    "VALUES ('acme', 'chat', 'text', '{}', '2026-10-01', '2026-10-01')"
                )
            )
        command.upgrade(cfg, revision)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT user_created FROM catalog_user_entry")).scalar_one() in (0, False)
    finally:
        engine.dispose()

    command.downgrade(cfg, parent)
    assert "user_created" not in _columns(db_path)
