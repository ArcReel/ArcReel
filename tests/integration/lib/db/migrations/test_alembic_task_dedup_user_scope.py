"""Alembic coverage for user-scoped active task deduplication."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from alembic import command

REVISION = "7f2c4d8a91b3"
DOWN_REVISION = "26c2f9d11c8e"


def _dedup_sql(db_path: Path) -> str:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        sql = conn.execute(
            sa.text("SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_tasks_dedupe_active'")
        ).scalar_one()
    engine.dispose()
    return str(sql)


def test_upgrade_and_downgrade_task_dedup_user_scope(alembic_cfg) -> None:
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, REVISION)
    assert "user_id" in _dedup_sql(db_path)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO users (id, username, role, is_active, created_at, updated_at) "
                "VALUES ('other', 'other', 'user', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        for task_id, user_id in (("T-default", "default"), ("T-other", "other")):
            conn.execute(
                sa.text(
                    "INSERT INTO tasks (task_id, project_name, task_type, media_type, resource_id, status, "
                    "source, queued_at, updated_at, user_id) VALUES (:task_id, 'demo', 'text_episode_script', "
                    "'text', 'episode-1', 'queued', 'embedded', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :user_id)"
                ),
                {"task_id": task_id, "user_id": user_id},
            )
    engine.dispose()

    command.downgrade(cfg, DOWN_REVISION)
    assert "user_id" not in _dedup_sql(db_path)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        statuses = {
            str(row.task_id): str(row.status)
            for row in conn.execute(sa.text("SELECT task_id, status FROM tasks WHERE task_id LIKE 'T-%'"))
        }
    engine.dispose()
    assert sorted(statuses.values()) == ["cancelled", "queued"]
