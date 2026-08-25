"""Alembic coverage for durable generation batches."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from alembic import command

REVISION = "26c2f9d11c8e"
DOWN_REVISION = "b3f9c07ae214"


def _table_names(db_path: Path) -> set[str]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        names = set(sa.inspect(conn).get_table_names())
    engine.dispose()
    return names


def test_upgrade_adds_batches_without_changing_existing_tasks(alembic_cfg) -> None:
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, DOWN_REVISION)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO tasks (task_id, project_name, task_type, media_type, resource_id, status, "
                "source, queued_at, updated_at) VALUES ('T-old', 'demo', 'storyboard', 'image', 'E1S01', "
                "'succeeded', 'webui', '2026-08-25 00:00:00', '2026-08-25 00:00:00')"
            )
        )
    engine.dispose()

    command.upgrade(cfg, REVISION)

    assert {"batches", "batch_tasks"} <= _table_names(db_path)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT task_id, batch_id, status FROM tasks WHERE task_id='T-old'")).one()
        assert tuple(row) == ("T-old", None, "succeeded")
        batch_task_fks = {fk["referred_table"] for fk in sa.inspect(conn).get_foreign_keys("batch_tasks")}
        assert batch_task_fks == {"batches", "tasks"}
        indexes = {index["name"] for index in sa.inspect(conn).get_indexes("tasks")}
        assert "ix_tasks_batch_id" in indexes
        raw_indexes = {row[1] for row in conn.execute(sa.text("PRAGMA index_list('tasks')")).fetchall()}
        assert "idx_tasks_dedupe_active" in raw_indexes
    engine.dispose()


def test_downgrade_removes_batch_schema_and_preserves_task_indexes(alembic_cfg) -> None:
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, REVISION)
    command.downgrade(cfg, DOWN_REVISION)

    assert {"batches", "batch_tasks"}.isdisjoint(_table_names(db_path))
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        columns = {column["name"] for column in sa.inspect(conn).get_columns("tasks")}
        indexes = {row[1] for row in conn.execute(sa.text("PRAGMA index_list('tasks')")).fetchall()}
    engine.dispose()
    assert "batch_id" not in columns
    assert "idx_tasks_dedupe_active" in indexes
