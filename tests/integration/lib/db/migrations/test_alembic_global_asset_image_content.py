"""Alembic 迁移：全局资产与衍生新增图片内容列，存量行保持为空，降级时删除。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_COLUMNS = {"image_digest", "image_content_id", "image_storage"}


def _columns(db_path: Path, table: str) -> set[str]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        return {column["name"] for column in sa.inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def test_upgrade_adds_empty_image_columns_and_downgrade_drops_them(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions("*_global_asset_image_content.py")

    command.upgrade(cfg, parent)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO assets (id, type, name, description, voice_style, image_path, created_at, updated_at)"
                    " VALUES ('a1', 'character', 'Alice', '', '', 'global_assets/character/a.png',"
                    " '2026-01-01', '2026-01-01')"
                )
            )
        command.upgrade(cfg, revision)
        for table in ("assets", "asset_derivatives"):
            assert _columns(db_path, table) >= _COLUMNS
        with engine.connect() as conn:
            row = conn.execute(sa.text("SELECT image_path, image_digest, image_storage FROM assets")).one()
        assert row == ("global_assets/character/a.png", None, None)
    finally:
        engine.dispose()

    command.downgrade(cfg, parent)
    for table in ("assets", "asset_derivatives"):
        assert not _COLUMNS & _columns(db_path, table)
