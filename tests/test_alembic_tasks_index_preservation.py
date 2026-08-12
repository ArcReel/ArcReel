"""SQLite 上 tasks 表重建型 downgrade 的索引存活回归。

SQLAlchemy 反射不出 ``idx_tasks_dedupe_active`` 这种表达式型 partial unique 索引，凡是走
batch 重建表的 downgrade 都可能把它静默丢掉——丢了等于去重闸失效，同一资源可并发起两个活动
任务。断言从 head 逐级降级时它一路存活。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEDUPE_INDEX = "idx_tasks_dedupe_active"

# (被测迁移, 其 down_revision, 该迁移有意删除因而允许消失的索引)
REBUILD_MIGRATIONS = [
    ("c4a91f7d2b18", "b7f2c41d9a30", frozenset()),
    ("285dbe1e9824", "8b1e8a1290ca", frozenset({"idx_tasks_status_provider_queued"})),
    ("548f6ca3e91c", "c9b24204c0de", frozenset()),
]


@pytest.fixture
def alembic_cfg(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    # alembic env.py 的 fileConfig 默认 disable_existing_loggers=True，会禁掉测试进程已注册的
    # logger，污染后续 caplog 测试。
    import logging.config

    real_file_config = logging.config.fileConfig
    monkeypatch.setattr(
        logging.config,
        "fileConfig",
        lambda *args, **kwargs: real_file_config(*args, **{**kwargs, "disable_existing_loggers": False}),
    )
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg, db_path


def _tasks_indexes(db_path: Path) -> set[str]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        rows = conn.execute(sa.text("PRAGMA index_list('tasks')")).fetchall()
    engine.dispose()
    return {r[1] for r in rows}


@pytest.mark.parametrize(("revision", "down_revision", "intentional_drops"), REBUILD_MIGRATIONS)
def test_downgrade_keeps_indexes(alembic_cfg, revision, down_revision, intentional_drops):
    """降级一步后，除该迁移自身显式删除的索引外，降级前的索引一个不少。

    从 head 逐级降到被测迁移，而不是从 base 升上来：``b942e8c5d545`` 的升级路径本身就会丢掉
    去重索引（直到 ``a3f1c9b27e54`` 重建），从 base 升起来的断言在早期迁移上是空的。
    """
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")
    command.downgrade(cfg, revision)

    before = _tasks_indexes(db_path)
    assert DEDUPE_INDEX in before

    command.downgrade(cfg, down_revision)
    after = _tasks_indexes(db_path)

    assert before - after == set(intentional_drops)
