"""Alembic c4a91f7d2b18（tasks.provider_endpoint）双向迁移测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REVISION = "c4a91f7d2b18"
DOWN_REVISION = "b7f2c41d9a30"


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


def _tasks_columns(db_path: Path) -> set[str]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        rows = conn.execute(sa.text("PRAGMA table_info(tasks)")).fetchall()
    engine.dispose()
    return {r[1] for r in rows}


def test_upgrade_adds_nullable_provider_endpoint(alembic_cfg):
    """升级加列；存量任务该列为 NULL —— 无从比对 endpoint，续跑照常接续。"""
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, DOWN_REVISION)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO tasks (task_id, project_name, task_type, media_type, resource_id, status, "
                "source, queued_at, updated_at) VALUES ('T-old', 'demo', 'video', 'video', 'r1', "
                "'running', 'webui', '2026-08-05 00:00:00', '2026-08-05 00:00:00')"
            )
        )
    engine.dispose()

    command.upgrade(cfg, REVISION)

    assert "provider_endpoint" in _tasks_columns(db_path)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        value = conn.execute(sa.text("SELECT provider_endpoint FROM tasks WHERE task_id = 'T-old'")).scalar()
    engine.dispose()
    assert value is None


def test_downgrade_drops_column(alembic_cfg):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, REVISION)
    assert "provider_endpoint" in _tasks_columns(db_path)

    command.downgrade(cfg, DOWN_REVISION)
    assert "provider_endpoint" not in _tasks_columns(db_path)
