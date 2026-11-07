"""Downgrade regression for Alembic 3649100774fa, dropping task_events.

Downgrade must recreate task_events with the original named foreign key
(``fk_task_events_task_id``): earlier migration b942e8c5d545 drops that constraint by name on downgrade.
Mismatches break the whole downward chain, invisible to ``upgrade head``-only tests.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command

#: FK name from creation migration b942e8c5d545; downgrade recreation must match.
_FK_NAME = "fk_task_events_task_id"

#: Parent revision of b942e8c5d545; reaching it crosses the named constraint-drop step.
_BELOW_FK_MIGRATION = "ecbb53758daa"


def test_downgrade_rebuilds_task_events_with_named_fk(alembic_cfg: tuple[Config, Path]):
    """Recreated task_events has the named FK and can continue downward through the migration dropping it by name."""
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        inspector = sa.inspect(engine)
        assert "task_events" not in inspector.get_table_names()

        command.downgrade(cfg, "b41d7c5e9a02")

        inspector = sa.inspect(engine)
        assert "task_events" in inspector.get_table_names()
        fk_names = {fk["name"] for fk in inspector.get_foreign_keys("task_events")}
        assert _FK_NAME in fk_names
        assert "idx_task_events_project_id" in {idx["name"] for idx in inspector.get_indexes("task_events")}
    finally:
        engine.dispose()

    # Continue below b942e8c5d545, which drops the FK by name; mismatches raise here.
    # ValueError: No such constraint。
    command.downgrade(cfg, _BELOW_FK_MIGRATION)

    # Reupgrade to head must not fail on leftover recreated tables.
    command.upgrade(cfg, "head")
