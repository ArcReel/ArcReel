"""Alembic 迁移：custom_endpoint 建表的 upgrade / downgrade。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_MIGRATION = "*_create_custom_endpoint_table.py"


_EXPECTED_COLUMNS = {
    "id",
    "definition",
    "kind",
    "schema_version",
    "media_type",
    "display_name",
    "created_at",
    "updated_at",
}


def _columns(engine: sa.Engine) -> set[str]:
    with engine.begin() as conn:
        rows = conn.execute(sa.text("PRAGMA table_info(custom_endpoint)")).fetchall()
    return {row[1] for row in rows}


def test_upgrade_creates_table(alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]):
    revision_id, parent_id = migration_revisions(_MIGRATION)
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, parent_id)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        assert not _columns(engine), "建表前不应存在 custom_endpoint"

        command.upgrade(cfg, revision_id)

        assert _columns(engine) == _EXPECTED_COLUMNS
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO custom_endpoint "
                    "(definition, kind, schema_version, media_type, display_name, created_at, updated_at) "
                    "VALUES ('{\"kind\": \"declarative\"}', 'declarative', '1.0.0', 'video', '示例端点', "
                    "'2026-08-27 00:00:00', '2026-08-27 00:00:00')"
                )
            )
            assigned_id = conn.execute(sa.text("SELECT id FROM custom_endpoint")).scalar_one()
        # 端点键 ce-<id> 由自增主键派生，插入时不用给 id
        assert assigned_id == 1
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
