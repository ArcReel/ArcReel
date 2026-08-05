"""Alembic 迁移 3649100774fa（drop task_events）的降级回归测试。

该迁移的 downgrade 需重建 task_events，且 FK 必须与建表迁移同名
（``fk_task_events_task_id``）：更早一级迁移 b942e8c5d545 的 downgrade 按名 drop 该约束，
名字对不上时整条下行链断在那里，而只测 ``upgrade head`` 的用例发现不了。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

pytestmark = pytest.mark.unit

#: 建表迁移 b942e8c5d545 使用的 FK 约束名，downgrade 重建时必须一致。
_FK_NAME = "fk_task_events_task_id"

#: b942e8c5d545 的父 revision——下行到这里就跨过了按名 drop 约束的那一步。
_BELOW_FK_MIGRATION = "ecbb53758daa"


@pytest.fixture
def alembic_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """指向项目 alembic 脚本，DB 用临时 sqlite（通过 DATABASE_URL，env.py 会读取）。"""
    repo_root = Path(__file__).resolve().parent.parent
    cfg = Config()
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg.attributes["_test_db_path"] = str(db_path)
    return cfg


def test_downgrade_rebuilds_task_events_with_named_fk(alembic_cfg: Config):
    """降级重建的 task_events 带具名 FK，且能继续下行跨过按名 drop 约束的迁移。"""
    command.upgrade(alembic_cfg, "head")

    engine = sa.create_engine(f"sqlite:///{alembic_cfg.attributes['_test_db_path']}")
    try:
        inspector = sa.inspect(engine)
        assert "task_events" not in inspector.get_table_names()

        command.downgrade(alembic_cfg, "b41d7c5e9a02")

        inspector = sa.inspect(engine)
        assert "task_events" in inspector.get_table_names()
        fk_names = {fk["name"] for fk in inspector.get_foreign_keys("task_events")}
        assert _FK_NAME in fk_names
        assert "idx_task_events_project_id" in {idx["name"] for idx in inspector.get_indexes("task_events")}
    finally:
        engine.dispose()

    # 继续下行跨过 b942e8c5d545（按名 drop 该 FK）——名字对不上时这里抛
    # ValueError: No such constraint。
    command.downgrade(alembic_cfg, _BELOW_FK_MIGRATION)

    # 重新升到 head 不应因重建过的表残留报错
    command.upgrade(alembic_cfg, "head")
