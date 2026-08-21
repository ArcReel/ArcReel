"""Alembic coverage for structured global-asset aliases."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REVISION = "9d96871835a1"
DOWN_REVISION = "ca8d7e31b6f2"


@pytest.fixture
def alembic_cfg(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg, db_path


def _tables(db_path: Path) -> set[str]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        tables = set(sa.inspect(connection).get_table_names())
    engine.dispose()
    return tables


def test_upgrade_and_downgrade_asset_aliases(alembic_cfg) -> None:
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, DOWN_REVISION)
    assert "asset_aliases" not in _tables(db_path)

    command.upgrade(cfg, REVISION)
    assert "asset_aliases" in _tables(db_path)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        columns = {column["name"] for column in sa.inspect(connection).get_columns("asset_aliases")}
        indexes = {index["name"] for index in sa.inspect(connection).get_indexes("asset_aliases")}
    engine.dispose()
    assert columns == {
        "id",
        "asset_id",
        "alias",
        "comparison_key",
        "origin",
        "sort_order",
        "created_at",
        "updated_at",
    }
    assert indexes == {"ix_asset_alias_comparison_key", "ix_asset_aliases_asset_id"}

    command.downgrade(cfg, DOWN_REVISION)
    assert "asset_aliases" not in _tables(db_path)
