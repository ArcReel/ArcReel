from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REVISION = "f19d7b4a2c61"
DOWN_REVISION = "f08c1d2e3a44"


@pytest.fixture
def alembic_cfg(tmp_path, monkeypatch):
    db_path = tmp_path / "account-center.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    import logging.config

    real_file_config = logging.config.fileConfig
    monkeypatch.setattr(
        logging.config,
        "fileConfig",
        lambda *args, **kwargs: real_file_config(*args, **{**kwargs, "disable_existing_loggers": False}),
    )
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg, db_path


def test_upgrade_preserves_legacy_credential_as_default_user(alembic_cfg) -> None:
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, DOWN_REVISION)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO provider_credential "
                "(provider, name, api_key, is_active, created_at, updated_at) "
                "VALUES ('gemini-aistudio', 'legacy', 'secret', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    engine.dispose()

    command.upgrade(cfg, REVISION)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        row = conn.execute(
            sa.text("SELECT user_id, api_key, is_active FROM provider_credential WHERE name='legacy'")
        ).one()
    engine.dispose()
    assert tuple(row) == ("default", "secret", 1)


def test_downgrade_keeps_rows_and_resolves_cross_user_active_collision(alembic_cfg) -> None:
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, REVISION)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO users (id, username, role, is_active, created_at, updated_at) "
                "VALUES ('alice', 'alice', 'admin', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO provider_credential "
                "(user_id, provider, name, api_key, is_active, created_at, updated_at) VALUES "
                "('default', 'gemini-aistudio', 'default-key', 'd', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('alice', 'gemini-aistudio', 'alice-key', 'a', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    engine.dispose()

    command.downgrade(cfg, DOWN_REVISION)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(sa.text("PRAGMA table_info(provider_credential)"))}
        rows = conn.execute(
            sa.text("SELECT name, is_active FROM provider_credential ORDER BY name")
        ).all()
    engine.dispose()
    assert "user_id" not in columns
    assert [tuple(row) for row in rows] == [("alice-key", 0), ("default-key", 1)]
