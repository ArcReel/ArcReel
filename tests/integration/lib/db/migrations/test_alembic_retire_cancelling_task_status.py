"""Alembic coverage for retiring the ``cancelling`` task status."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from alembic import command

MIGRATION_GLOB = "*_retire_cancelling_task_status.py"


def _dedup_sql(db_path: Path) -> str:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        sql = conn.execute(
            sa.text("SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_tasks_dedupe_active'")
        ).scalar_one()
    engine.dispose()
    return str(sql)


def _insert_task(
    conn: sa.Connection, task_id: str, status: str, cancelled_by: str | None, *, dependency_task_id: str | None = None
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO tasks (task_id, project_name, task_type, media_type, resource_id, status, cancelled_by, "
            "dependency_task_id, source, queued_at, updated_at, user_id) VALUES (:task_id, 'demo', 'storyboard', "
            "'image', :task_id, :status, :cancelled_by, :dependency_task_id, 'webui', CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP, 'default')"
        ),
        {"task_id": task_id, "status": status, "cancelled_by": cancelled_by, "dependency_task_id": dependency_task_id},
    )


def _rows(db_path: Path) -> dict[str, tuple[str, str | None, bool]]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        rows = {
            str(row.task_id): (str(row.status), row.cancelled_by, row.finished_at is not None)
            for row in conn.execute(sa.text("SELECT task_id, status, cancelled_by, finished_at FROM tasks"))
        }
    engine.dispose()
    return rows


def test_upgrade_converges_cancelling_rows_and_narrows_dedup_index(alembic_cfg, migration_revisions) -> None:
    cfg, db_path = alembic_cfg
    revision, down_revision = migration_revisions(MIGRATION_GLOB)
    command.upgrade(cfg, down_revision)
    assert "'cancelling'" in _dedup_sql(db_path)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        _insert_task(conn, "T-cancelling-user", "cancelling", "user")
        _insert_task(conn, "T-cancelling-cascade", "cancelling", "cascade")
        _insert_task(conn, "T-running", "running", None)
        _insert_task(conn, "T-queued", "queued", None)
    engine.dispose()

    command.upgrade(cfg, revision)

    assert _rows(db_path) == {
        "T-cancelling-user": ("cancelled", "user", True),
        "T-cancelling-cascade": ("cancelled", "cascade", True),
        "T-running": ("running", None, False),
        "T-queued": ("queued", None, False),
    }
    dedup = _dedup_sql(db_path)
    assert "'cancelling'" not in dedup
    assert "'queued', 'running'" in dedup
    assert "user_id" in dedup


def test_upgrade_cascades_cancelling_rows_to_queued_descendants(alembic_cfg, migration_revisions) -> None:
    """存量 cancelling 父行收敛时，排队中的下游（含孙辈）同步级联取消，不留永远认领不到的 queued。"""
    cfg, db_path = alembic_cfg
    revision, down_revision = migration_revisions(MIGRATION_GLOB)
    command.upgrade(cfg, down_revision)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        _insert_task(conn, "T-parent", "cancelling", "user")
        _insert_task(conn, "T-child", "queued", None, dependency_task_id="T-parent")
        _insert_task(conn, "T-grandchild", "queued", None, dependency_task_id="T-child")
        _insert_task(conn, "T-running-parent", "running", None)
        _insert_task(conn, "T-waiting-child", "queued", None, dependency_task_id="T-running-parent")
    engine.dispose()

    command.upgrade(cfg, revision)

    assert _rows(db_path) == {
        "T-parent": ("cancelled", "user", True),
        "T-child": ("cancelled", "cascade", True),
        "T-grandchild": ("cancelled", "cascade", True),
        "T-running-parent": ("running", None, False),
        "T-waiting-child": ("queued", None, False),
    }


def test_downgrade_restores_three_status_dedup_index(alembic_cfg, migration_revisions) -> None:
    cfg, db_path = alembic_cfg
    revision, down_revision = migration_revisions(MIGRATION_GLOB)
    command.upgrade(cfg, revision)

    command.downgrade(cfg, down_revision)

    dedup = _dedup_sql(db_path)
    assert "'queued', 'running', 'cancelling'" in dedup
    assert "user_id" in dedup
