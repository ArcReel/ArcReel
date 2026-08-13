"""Alembic migration for the reference-video execution checkpoint."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REVISION = "f6a41746c0de"
DOWN_REVISION = "d4f8b1c73a20"


@pytest.fixture
def alembic_cfg(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
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


def _columns(db_path: Path) -> set[str]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        rows = conn.execute(sa.text("PRAGMA table_info(tasks)")).fetchall()
    engine.dispose()
    return {row[1] for row in rows}


def test_upgrade_adds_nullable_execution_checkpoint_without_backfill(alembic_cfg) -> None:
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, DOWN_REVISION)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO tasks (task_id, project_name, task_type, media_type, resource_id, status, "
                "source, queued_at, updated_at) VALUES ('T-old', 'demo', 'reference_video', 'video', 'E1U1', "
                "'running', 'webui', '2026-08-13 00:00:00', '2026-08-13 00:00:00')"
            )
        )
    engine.dispose()

    command.upgrade(cfg, REVISION)

    assert "execution_checkpoint_json" in _columns(db_path)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        assert (
            conn.execute(sa.text("SELECT execution_checkpoint_json FROM tasks WHERE task_id='T-old'")).scalar() is None
        )
    engine.dispose()


def test_downgrade_drops_execution_checkpoint_and_preserves_active_dedupe_index(alembic_cfg) -> None:
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, REVISION)
    command.downgrade(cfg, DOWN_REVISION)

    assert "execution_checkpoint_json" not in _columns(db_path)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        indexes = {row[1] for row in conn.execute(sa.text("PRAGMA index_list('tasks')")).fetchall()}
    engine.dispose()
    assert "idx_tasks_dedupe_active" in indexes
