"""Alembic migration coverage for account-scoped managed provider settings."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

pytestmark = pytest.mark.unit

REVISION = "78d31ac4f962"
DOWN_REVISION = "3fa4bc012421"


@pytest.fixture
def alembic_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    repo_root = Path(__file__).resolve().parent.parent
    db_path = tmp_path / "managed-provider-config.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config()
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    cfg.attributes["_test_db_path"] = str(db_path)
    return cfg


def _engine(cfg: Config) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{cfg.attributes['_test_db_path']}")


def test_upgrade_creates_account_scoped_table_and_enforces_identity(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, REVISION)
    engine = _engine(alembic_cfg)
    try:
        inspector = sa.inspect(engine)
        assert "managed_provider_config" in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("managed_provider_config")}
        assert columns == {
            "id",
            "user_id",
            "provider",
            "key",
            "value",
            "is_secret",
            "management_source",
            "management_revision",
            "updated_at",
        }

        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO managed_provider_config "
                    "(user_id, provider, key, value, is_secret, management_source, "
                    "management_revision, updated_at) VALUES "
                    "('default', 'gemini-vertex', 'project_id', 'project-a', 0, "
                    "'account_center', 1, CURRENT_TIMESTAMP)"
                )
            )

        with engine.connect() as conn, pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO managed_provider_config "
                    "(user_id, provider, key, value, is_secret, management_source, "
                    "management_revision, updated_at) VALUES "
                    "('default', 'gemini-vertex', 'project_id', 'project-b', 0, "
                    "'account_center', 2, CURRENT_TIMESTAMP)"
                )
            )
    finally:
        engine.dispose()


def test_account_delete_cascades_and_downgrade_removes_table(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, REVISION)
    engine = _engine(alembic_cfg)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("PRAGMA foreign_keys=ON"))
            conn.execute(
                sa.text(
                    "INSERT INTO users (id, username, role, is_active, created_at, updated_at) "
                    "VALUES ('managed-user', 'managed-user', 'user', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO managed_provider_config "
                    "(user_id, provider, key, value, is_secret, management_source, "
                    "management_revision, updated_at) VALUES "
                    "('managed-user', 'openai', 'api_key', 'secret', 1, "
                    "'account_center', 1, CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(sa.text("DELETE FROM users WHERE id='managed-user'"))
            remaining = conn.execute(
                sa.text("SELECT count(*) FROM managed_provider_config WHERE user_id='managed-user'")
            ).scalar_one()
        assert remaining == 0
    finally:
        engine.dispose()

    command.downgrade(alembic_cfg, DOWN_REVISION)
    engine = _engine(alembic_cfg)
    try:
        assert "managed_provider_config" not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()
