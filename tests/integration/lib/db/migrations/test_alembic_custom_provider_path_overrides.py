"""Alembic 迁移：custom_provider_model.path_overrides 的 upgrade / downgrade。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_COL = "path_overrides"
_GLOB = "*_add_path_overrides_to_custom_provider_model.py"

_INSERT_PROVIDER = (
    "INSERT INTO custom_provider "
    "(id, display_name, discovery_format, base_url, api_key, created_at, updated_at) "
    "VALUES (1, 'P', 'openai', 'https://x', 'k', '2026-10-15 00:00:00', '2026-10-15 00:00:00')"
)
_INSERT_MODEL = (
    "INSERT INTO custom_provider_model "
    "(id, provider_id, model_id, display_name, endpoint, is_default, is_enabled, created_at, updated_at) "
    "VALUES (1, 1, 'veo-3', 'Veo', 'openrouter-video', 0, 1, "
    "'2026-10-15 00:00:00', '2026-10-15 00:00:00')"
)


def _columns(engine: sa.Engine) -> set[str]:
    with engine.begin() as conn:
        rows = conn.execute(sa.text("PRAGMA table_info(custom_provider_model)")).fetchall()
    return {r[1] for r in rows}


def test_upgrade_leaves_existing_models_without_overrides(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    revision, parent = migration_revisions(_GLOB)
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, parent)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(_INSERT_PROVIDER))
            conn.execute(sa.text(_INSERT_MODEL))

        command.upgrade(cfg, revision)

        with engine.begin() as conn:
            value = conn.execute(sa.text(f"SELECT {_COL} FROM custom_provider_model WHERE id = 1")).scalar_one()
        assert value is None
    finally:
        engine.dispose()


def test_downgrade_drops_the_column_and_keeps_rows(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    revision, parent = migration_revisions(_GLOB)
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, revision)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(_INSERT_PROVIDER))
            conn.execute(sa.text(_INSERT_MODEL))
            conn.execute(
                sa.text(f"UPDATE custom_provider_model SET {_COL} = :v WHERE id = 1"),
                {"v": json.dumps({"poll": "/api/videos/{{ task_id }}"})},
            )

        command.downgrade(cfg, parent)

        assert _COL not in _columns(engine)
        with engine.begin() as conn:
            model_id = conn.execute(sa.text("SELECT model_id FROM custom_provider_model WHERE id = 1")).scalar_one()
        assert model_id == "veo-3"
    finally:
        engine.dispose()
