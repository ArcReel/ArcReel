"""SQLite tasks-table rebuilding downgrade index-survival regressions.

SQLAlchemy cannot reflect expression-based partial unique indexes such as ``idx_tasks_dedupe_active``. Any batch-table-rebuilding
downgrade can silently drop them, disabling deduplication and allowing two active tasks for one resource.
Guard both individual rebuilding migrations with paired assertions identifying the culprit, and the complete head→base chain
without relying on enumeration, catching the same defect in newly added migrations too.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.script import ScriptDirectory

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[5]
DEDUPE_INDEX = "idx_tasks_dedupe_active"

# tasks indexes explicitly dropped by each downgrade are expected reversions, distinct from silent table-rebuild losses.
INTENTIONAL_TASKS_INDEX_DROPS = {
    "26c2f9d11c8e": frozenset({"ix_tasks_batch_id"}),
    "285dbe1e9824": frozenset({"idx_tasks_status_provider_queued"}),
    "ea2e1a477bbf": frozenset({"ix_tasks_user_id"}),
}

# Migrations whose downgrade batch-rebuilds tasks.
REBUILD_MIGRATIONS = [
    "26c2f9d11c8e",
    "d4f8b1c73a20",
    "c4a91f7d2b18",
    "285dbe1e9824",
    "548f6ca3e91c",
    "ea2e1a477bbf",
]

# Initial schema drops tasks itself; full-chain inspection stops here.
INITIAL_SCHEMA_REVISION = "156fe0aa0414"


def _tasks_indexes(db_path: Path) -> set[str] | None:
    """Index names on tasks, or None if the table does not exist."""
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        exists = conn.execute(sa.text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tasks'")).first()
        rows = conn.execute(sa.text("PRAGMA index_list('tasks')")).fetchall() if exists else []
    engine.dispose()
    return {r[1] for r in rows} if exists else None


@pytest.mark.parametrize("revision", REBUILD_MIGRATIONS)
def test_downgrade_keeps_indexes(alembic_cfg, revision):
    """After one downgrade, retain all prior indexes except those explicitly removed by that migration.

    Descend from head to the tested revision rather than upgrading from base: upgrades also lose the deduplication index at
    ``ea2e1a477bbf`` and restore it only at ``a3f1c9b27e54``. From base, it would be absent in early migrations and
    assertions empty. Returning to base-upward setup silently removes these tests' meaning.
    """
    cfg, db_path = alembic_cfg
    down_revision = ScriptDirectory.from_config(cfg).get_revision(revision).down_revision
    command.upgrade(cfg, "head")
    command.downgrade(cfg, revision)

    before = _tasks_indexes(db_path)
    assert before is not None
    assert DEDUPE_INDEX in before

    command.downgrade(cfg, str(down_revision))
    after = _tasks_indexes(db_path)
    assert after is not None

    assert before - after == INTENTIONAL_TASKS_INDEX_DROPS.get(revision, frozenset())


def test_full_chain_downgrade_only_drops_intentional_indexes(alembic_cfg):
    """Descend head to base, losing only explicitly removed tasks indexes at each step.

    Independent of ``REBUILD_MIGRATIONS`` completeness: new migrations introducing the same pattern fail here
    even without registration above. Deduplication indexes survive through the table-dropping initial schema.
    """
    cfg, db_path = alembic_cfg
    script = ScriptDirectory.from_config(cfg)
    command.upgrade(cfg, "head")

    before = _tasks_indexes(db_path)
    assert before is not None
    assert DEDUPE_INDEX in before

    for revision in (s.revision for s in script.walk_revisions("base", "heads")):
        down_revision = script.get_revision(revision).down_revision or "base"
        command.downgrade(cfg, str(down_revision))
        after = _tasks_indexes(db_path)
        if after is None:
            assert revision == INITIAL_SCHEMA_REVISION
            assert DEDUPE_INDEX in before
            return

        assert before - after == INTENTIONAL_TASKS_INDEX_DROPS.get(revision, frozenset()), (
            f"{revision} 降级丢失了未登记的 tasks 索引"
        )
        before = after

    pytest.fail(f"降到 base 也没走到 {INITIAL_SCHEMA_REVISION} 的删表步骤")
