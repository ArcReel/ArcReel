"""Bidirectional Alembic d4f8b1c73a20 (tasks.submitted_base_url) migration tests."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[5]
REVISION = "d4f8b1c73a20"
DOWN_REVISION = "9c41ad2f7be5"


def _tasks_columns(db_path: Path) -> set[str]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        rows = conn.execute(sa.text("PRAGMA table_info(tasks)")).fetchall()
    engine.dispose()
    return {r[1] for r in rows}


def _tasks_indexes(db_path: Path) -> set[str]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        rows = conn.execute(sa.text("PRAGMA index_list('tasks')")).fetchall()
    engine.dispose()
    return {r[1] for r in rows}


def test_upgrade_adds_nullable_submitted_base_url(alembic_cfg):
    """Upgrade adds the column with NULL existing tasks; without recorded submission hosts, resume polls current configured hosts."""
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, DOWN_REVISION)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO tasks (task_id, project_name, task_type, media_type, resource_id, status, "
                "source, queued_at, updated_at) VALUES ('T-old', 'demo', 'video', 'video', 'r1', "
                "'running', 'webui', '2026-08-12 00:00:00', '2026-08-12 00:00:00')"
            )
        )
    engine.dispose()

    command.upgrade(cfg, REVISION)

    assert "submitted_base_url" in _tasks_columns(db_path)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        value = conn.execute(sa.text("SELECT submitted_base_url FROM tasks WHERE task_id = 'T-old'")).scalar()
    engine.dispose()
    assert value is None


def test_downgrade_drops_column(alembic_cfg):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, REVISION)
    assert "submitted_base_url" in _tasks_columns(db_path)

    command.downgrade(cfg, DOWN_REVISION)
    assert "submitted_base_url" not in _tasks_columns(db_path)


def test_downgrade_keeps_dedupe_index(alembic_cfg):
    """Downgrade table rebuild preserves deduplication indexes; losing unreflectable expression-based partial indexes would disable the deduplication gate."""
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, REVISION)
    assert "idx_tasks_dedupe_active" in _tasks_indexes(db_path)

    command.downgrade(cfg, DOWN_REVISION)
    assert "idx_tasks_dedupe_active" in _tasks_indexes(db_path)
