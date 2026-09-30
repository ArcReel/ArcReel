"""分享提交记录迁移往返、每端点一条记录及端点删除级联。"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command


def test_submission_migration_round_trip(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    revision, parent = migration_revisions("*_create_market_submission_table.py")
    cfg, path = alembic_cfg
    command.upgrade(cfg, revision)
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        metadata = sa.MetaData()
        metadata.reflect(engine)
        endpoints = metadata.tables["custom_endpoint"]
        records = metadata.tables["market_submission"]
        assert set(records.columns.keys()) == {
            "custom_endpoint_id",
            "token",
            "type",
            "slug",
            "status",
            "pr_url",
            "submitted_at",
        }
        values = {
            "token": "3f1c" + "0" * 28,
            "type": "endpoint",
            "slug": "example",
            "status": "open",
            "pr_url": "https://github.com/ArcReel/arcreel-market/pull/1",
            "submitted_at": datetime.now(UTC),
        }
        with engine.begin() as conn:
            conn.execute(
                endpoints.insert().values(
                    id=1,
                    definition={},
                    kind="declarative",
                    schema_version="1.1.0",
                    media_type="video",
                    display_name="Example",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            conn.execute(records.insert().values(custom_endpoint_id=1, **values))
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
            conn.execute(records.insert().values(custom_endpoint_id=1, **values))
        with engine.begin() as conn:
            conn.execute(sa.text("PRAGMA foreign_keys=ON"))
            conn.execute(endpoints.delete().where(endpoints.c.id == 1))
            assert conn.scalar(sa.select(sa.func.count()).select_from(records)) == 0
        command.downgrade(cfg, parent)
        assert "market_submission" not in sa.inspect(engine).get_table_names()
        command.upgrade(cfg, revision)
        assert "market_submission" in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()
