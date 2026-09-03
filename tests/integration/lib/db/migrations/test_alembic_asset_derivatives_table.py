"""Alembic 迁移：asset_derivatives 建表的 upgrade / downgrade 与外键级联。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_MIGRATION = "*_create_asset_derivatives_table.py"

_EXPECTED_COLUMNS = {
    "id",
    "asset_id",
    "name",
    "description",
    "image_path",
    "created_at",
    "updated_at",
}

_INSERT_ASSET = sa.text(
    "INSERT INTO assets (id, type, name, description, voice_style, source_project, created_at, updated_at) "
    "VALUES (:id, 'character', :name, '', '', NULL, '2026-09-04 00:00:00', '2026-09-04 00:00:00')"
)
_INSERT_DERIVATIVE = sa.text(
    "INSERT INTO asset_derivatives (id, asset_id, name, description, image_path, created_at, updated_at) "
    "VALUES (:id, :asset_id, :name, :description, :image_path, "
    "'2026-09-04 00:00:00', '2026-09-04 00:00:00')"
)


def _columns(engine: sa.Engine) -> set[str]:
    with engine.begin() as conn:
        rows = conn.execute(sa.text("PRAGMA table_info(asset_derivatives)")).fetchall()
    return {row[1] for row in rows}


def test_upgrade_creates_table(alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]):
    revision_id, parent_id = migration_revisions(_MIGRATION)
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, parent_id)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        assert not _columns(engine), "建表前不应存在 asset_derivatives"

        command.upgrade(cfg, revision_id)

        assert _columns(engine) == _EXPECTED_COLUMNS
        with engine.begin() as conn:
            conn.execute(_INSERT_ASSET, {"id": "a1", "name": "王"})
            conn.execute(
                _INSERT_DERIVATIVE,
                {
                    "id": "d1",
                    "asset_id": "a1",
                    "name": "战斗装",
                    "description": "黑甲",
                    "image_path": "_global_assets/character/abc.png",
                },
            )
            stored = conn.execute(
                sa.text("SELECT asset_id, name, description, image_path FROM asset_derivatives")
            ).one()
        assert stored == ("a1", "战斗装", "黑甲", "_global_assets/character/abc.png")
    finally:
        engine.dispose()


def test_derivative_name_is_unique_within_one_asset(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
):
    """同一条资产下衍生名唯一；不同资产下的同名衍生互不冲突。"""
    revision_id, _parent_id = migration_revisions(_MIGRATION)
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, revision_id)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(_INSERT_ASSET, {"id": "a1", "name": "王"})
            conn.execute(_INSERT_ASSET, {"id": "a2", "name": "李"})
            row = {"name": "战斗装", "description": "", "image_path": None}
            conn.execute(_INSERT_DERIVATIVE, {"id": "d1", "asset_id": "a1", **row})
            conn.execute(_INSERT_DERIVATIVE, {"id": "d2", "asset_id": "a2", **row})

        with engine.begin() as conn, pytest.raises(sa.exc.IntegrityError):
            conn.execute(_INSERT_DERIVATIVE, {"id": "d3", "asset_id": "a1", **row})
    finally:
        engine.dispose()


def test_deleting_an_asset_cascades_to_its_derivatives(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
):
    """衍生行随本体资产被外键级联删除，不需要应用层逐条清理。"""
    revision_id, _parent_id = migration_revisions(_MIGRATION)
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, revision_id)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            # SQLite 默认不强制外键；生产 engine 在连接建立时开这个 pragma。
            conn.execute(sa.text("PRAGMA foreign_keys=ON"))
            conn.execute(_INSERT_ASSET, {"id": "a1", "name": "王"})
            conn.execute(
                _INSERT_DERIVATIVE,
                {"id": "d1", "asset_id": "a1", "name": "战斗装", "description": "", "image_path": None},
            )
            conn.execute(sa.text("DELETE FROM assets WHERE id = 'a1'"))
            remaining = conn.execute(sa.text("SELECT COUNT(*) FROM asset_derivatives")).scalar_one()
        assert remaining == 0
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
