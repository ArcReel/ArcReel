"""Upgrade/downgrade regressions for Alembic e167b56a3e79 (tasks.resource_type and deduplication index).

Active cross-resource_type key collisions are legal after upgrade but incompatible with the narrow downgraded index.
Soft-cancel the later queued task first or recreating the unique index fails with a constraint violation.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_MIGRATION = "*_add_resource_type_column_to_tasks_for_.py"


def _insert_task(
    conn: sa.Connection,
    *,
    task_id: str,
    resource_type: str,
    resource_id: str,
    queued_at: str,
    status: str = "queued",
) -> None:
    conn.execute(
        sa.text(
            """
            INSERT INTO tasks
                (task_id, project_name, task_type, media_type, resource_id, resource_type,
                 status, source, queued_at, updated_at)
            VALUES
                (:task_id, 'demo', 'image_edit', 'image', :resource_id, :resource_type,
                 :status, 'webui', :queued_at, :queued_at)
            """
        ),
        {
            "task_id": task_id,
            "resource_id": resource_id,
            "resource_type": resource_type,
            "status": status,
            "queued_at": queued_at,
        },
    )


def test_downgrade_collapses_conflicting_active_tasks(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    """Cross-resource_type same-named active tasks allowed after upgrade must reduce to one through soft cancellation before downgrade."""
    revision_id, parent_revision_id = migration_revisions(_MIGRATION)
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, revision_id)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            # Coexisting character 玉佩 and prop 玉佩 active image_edit tasks in one project:
            # project_name / task_type / resource_id / script_file match; only resource_type differs.
            _insert_task(
                conn,
                task_id="task-a",
                resource_type="character",
                resource_id="玉佩",
                queued_at="2026-07-16 10:00:00",
            )
            _insert_task(
                conn,
                task_id="task-b",
                resource_type="prop",
                resource_id="玉佩",
                queued_at="2026-07-16 10:00:01",
            )

        # Before downgrade, the new resource_type-inclusive index permits these rows.
        with engine.begin() as conn:
            statuses = {row[0]: row[1] for row in conn.execute(sa.text("SELECT task_id, status FROM tasks")).fetchall()}
        assert statuses == {"task-a": "queued", "task-b": "queued"}

        command.downgrade(cfg, parent_revision_id)

        with engine.begin() as conn:
            rows = {
                row[0]: row
                for row in conn.execute(
                    sa.text("SELECT task_id, status, cancelled_by, error_message FROM tasks")
                ).fetchall()
            }

        # Retain the earlier task state; soft-cancel the later one without hard deletion.
        assert rows["task-a"][1] == "queued"
        assert rows["task-a"][2] is None
        assert rows["task-b"][1] == "cancelled"
        assert rows["task-b"][2] == "system"
        assert rows["task-b"][3] is not None

        # Restore the narrow index and remove resource_type.
        with engine.begin() as conn:
            columns = {row[1] for row in conn.execute(sa.text("PRAGMA table_info(tasks)")).fetchall()}
        assert "resource_type" not in columns
    finally:
        engine.dispose()

    # Reupgrade must not fail on remaining data.
    command.upgrade(cfg, revision_id)


def test_downgrade_without_conflict_is_noop_for_active_tasks(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    """Without cross-resource_type collisions, downgrade preserves every active task state."""
    revision_id, parent_revision_id = migration_revisions(_MIGRATION)
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, revision_id)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            _insert_task(
                conn,
                task_id="task-a",
                resource_type="character",
                resource_id="Alice",
                queued_at="2026-07-16 10:00:00",
            )
            _insert_task(
                conn,
                task_id="task-b",
                resource_type="prop",
                resource_id="玉佩",
                queued_at="2026-07-16 10:00:01",
            )

        command.downgrade(cfg, parent_revision_id)

        with engine.begin() as conn:
            statuses = {row[0]: row[1] for row in conn.execute(sa.text("SELECT task_id, status FROM tasks")).fetchall()}
        assert statuses == {"task-a": "queued", "task-b": "queued"}
    finally:
        engine.dispose()
