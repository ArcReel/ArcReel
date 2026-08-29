"""Alembic 迁移：api_calls.last_provider_response 加列与回退。"""

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command


def _columns(engine: sa.Engine) -> set[str]:
    with engine.begin() as connection:
        return {row[1] for row in connection.execute(sa.text("PRAGMA table_info(api_calls)"))}


def test_last_provider_response_upgrade_and_downgrade(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
):
    revision, parent = migration_revisions("*_add_last_provider_response.py")
    config, database = alembic_cfg
    command.upgrade(config, parent)
    engine = sa.create_engine(f"sqlite:///{database}")
    try:
        assert "last_provider_response" not in _columns(engine)
        command.upgrade(config, revision)
        assert "last_provider_response" in _columns(engine)
        command.downgrade(config, parent)
        assert "last_provider_response" not in _columns(engine)
    finally:
        engine.dispose()
