"""安装记录迁移往返、唯一约束及端点删除级联。"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command


def test_installation_migration_round_trip(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    revision, parent = migration_revisions("*_create_market_installation_table.py")
    cfg, path = alembic_cfg
    command.upgrade(cfg, revision)
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        metadata = sa.MetaData()
        metadata.reflect(engine)
        endpoints = metadata.tables["custom_endpoint"]
        records = metadata.tables["market_installation"]
        assert set(records.columns.keys()) == {
            "custom_endpoint_id",
            "source_key",
            "slug",
            "installed_version",
            "installed_digest",
            "installed_at",
        }
        with engine.begin() as conn:
            conn.execute(sa.text("PRAGMA foreign_keys=ON"))
            for endpoint_id in (1, 2):
                conn.execute(
                    endpoints.insert().values(
                        id=endpoint_id,
                        definition={},
                        kind="declarative",
                        schema_version="1.1.0",
                        media_type="video",
                        display_name="Example",
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                )
            values = {
                "source_key": "url:market",
                "slug": "example",
                "installed_version": "1.0.0",
                "installed_digest": "a" * 64,
                "installed_at": datetime.now(UTC),
            }
            conn.execute(records.insert().values(custom_endpoint_id=1, **values))
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
            conn.execute(records.insert().values(custom_endpoint_id=2, **values))
        with engine.begin() as conn:
            conn.execute(endpoints.delete().where(endpoints.c.id == 1))
            assert conn.scalar(sa.select(sa.func.count()).select_from(records)) == 0
        command.downgrade(cfg, parent)
        assert "market_installation" not in sa.inspect(engine).get_table_names()
        command.upgrade(cfg, revision)
        assert "market_installation" in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()
