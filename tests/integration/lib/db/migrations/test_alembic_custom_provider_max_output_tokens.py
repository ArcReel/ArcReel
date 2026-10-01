"""Alembic 迁移：custom_provider_model.max_output_tokens 的 upgrade / downgrade。"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_REVISION = "e3b81f6c4a27"
_PARENT = "d7e4a2c95b10"
_COL = "max_output_tokens"

_INSERT_PROVIDER = (
    "INSERT INTO custom_provider "
    "(id, display_name, discovery_format, base_url, api_key, created_at, updated_at) "
    "VALUES (1, 'P', 'openai', 'https://x', 'k', '2026-10-01 00:00:00', '2026-10-01 00:00:00')"
)
_INSERT_MODEL = (
    "INSERT INTO custom_provider_model "
    "(id, provider_id, model_id, display_name, endpoint, is_default, is_enabled, created_at, updated_at) "
    "VALUES (1, 1, 'my-llm', 'My LLM', 'openai-chat', 1, 1, "
    "'2026-10-01 00:00:00', '2026-10-01 00:00:00')"
)


def _columns(engine: sa.Engine) -> set[str]:
    with engine.begin() as conn:
        rows = conn.execute(sa.text("PRAGMA table_info(custom_provider_model)")).fetchall()
    return {r[1] for r in rows}


def test_upgrade_leaves_existing_models_unregistered(alembic_cfg: tuple[Config, Path]):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, _PARENT)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(_INSERT_PROVIDER))
            conn.execute(sa.text(_INSERT_MODEL))

        command.upgrade(cfg, _REVISION)

        with engine.begin() as conn:
            value = conn.execute(sa.text(f"SELECT {_COL} FROM custom_provider_model WHERE id = 1")).scalar_one()
        assert value is None
    finally:
        engine.dispose()


def test_downgrade_drops_the_column(alembic_cfg: tuple[Config, Path]):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, _REVISION)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        assert _COL in _columns(engine)
        command.downgrade(cfg, _PARENT)
        assert _COL not in _columns(engine)
    finally:
        engine.dispose()
