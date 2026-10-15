"""Alembic 迁移：Agent 凭证新增倍率与档位的条目绑定，存量倍率为 1、绑定为空。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_GLOB = "*_agent_credential_pricing.py"
_INSERT = (
    "INSERT INTO agent_anthropic_credentials "
    "(id, user_id, preset_id, display_name, base_url, api_key, model, is_active, created_at, updated_at) "
    "VALUES (1, 'default', 'deepseek', 'DeepSeek', 'https://api.deepseek.com/anthropic', 'sk', 'deepseek-v4-pro', 1, "
    "'2026-10-15 00:00:00', '2026-10-15 00:00:00')"
)


def _columns(engine: sa.Engine) -> set[str]:
    with engine.begin() as conn:
        return {row[1] for row in conn.execute(sa.text("PRAGMA table_info(agent_anthropic_credentials)")).fetchall()}


def test_upgrade_gives_existing_credentials_multiplier_one_and_no_bindings(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    revision, parent = migration_revisions(_GLOB)
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, parent)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(_INSERT))

        command.upgrade(cfg, revision)

        with engine.begin() as conn:
            row = conn.execute(
                sa.text("SELECT multiplier, tier_bindings, model FROM agent_anthropic_credentials WHERE id = 1")
            ).one()
        assert (row.multiplier, row.tier_bindings, row.model) == (1.0, None, "deepseek-v4-pro")
    finally:
        engine.dispose()


def test_downgrade_drops_both_columns_and_keeps_rows(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    revision, parent = migration_revisions(_GLOB)
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, revision)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(_INSERT))
            conn.execute(
                sa.text(
                    "UPDATE agent_anthropic_credentials SET multiplier = 0, "
                    """tier_bindings = '{"haiku": "deepseek/deepseek-v4-flash"}' WHERE id = 1"""
                )
            )

        command.downgrade(cfg, parent)

        assert not {"multiplier", "tier_bindings"} & _columns(engine)
        with engine.begin() as conn:
            model = conn.execute(sa.text("SELECT model FROM agent_anthropic_credentials WHERE id = 1")).scalar_one()
        assert model == "deepseek-v4-pro"
    finally:
        engine.dispose()
