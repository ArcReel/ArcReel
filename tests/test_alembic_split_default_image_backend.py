"""Alembic split_default_image_backend_setting 双向迁移测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def alembic_cfg(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
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


def _sync_engine(db_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{db_path}")


def test_upgrade_copies_legacy_setting_to_two_new_keys(alembic_cfg):
    """前置：写入旧 default_image_backend；执行迁移；验证两条新 key 同值。"""
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "eedf0aa985e6")

    engine = _sync_engine(db_path)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO system_setting (key, value, updated_at) "
                "VALUES ('default_image_backend', 'openai/gpt-image-1', CURRENT_TIMESTAMP)"
            )
        )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT key, value FROM system_setting WHERE key IN "
                "('default_image_backend', 'default_image_backend_t2i', 'default_image_backend_i2i')"
            )
        ).fetchall()
    settings = {r.key: r.value for r in rows}
    assert settings.get("default_image_backend_t2i") == "openai/gpt-image-1"
    assert settings.get("default_image_backend_i2i") == "openai/gpt-image-1"
    assert settings.get("default_image_backend") == "openai/gpt-image-1"


def test_upgrade_preserves_already_set_new_keys(alembic_cfg):
    """前置：旧 default_image_backend + _t2i 都已有；迁移不应覆盖 _t2i。"""
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "eedf0aa985e6")

    engine = _sync_engine(db_path)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO system_setting (key, value, updated_at) "
                "VALUES ('default_image_backend', 'openai/legacy', CURRENT_TIMESTAMP), "
                "('default_image_backend_t2i', 'openai/already-set', CURRENT_TIMESTAMP)"
            )
        )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT key, value FROM system_setting WHERE key IN "
                "('default_image_backend_t2i', 'default_image_backend_i2i')"
            )
        ).fetchall()
    settings = {r.key: r.value for r in rows}
    # _t2i 已存在 → 不覆盖
    assert settings.get("default_image_backend_t2i") == "openai/already-set"
    # _i2i 不存在 → 用 legacy 值填充
    assert settings.get("default_image_backend_i2i") == "openai/legacy"


def test_upgrade_no_op_when_no_legacy(alembic_cfg):
    """前置：未写入旧 default_image_backend；迁移应无副作用。"""
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")

    engine = _sync_engine(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT key FROM system_setting WHERE key IN "
                "('default_image_backend', 'default_image_backend_t2i', 'default_image_backend_i2i')"
            )
        ).fetchall()
    assert rows == []


def test_downgrade_drops_only_new_keys(alembic_cfg):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")

    engine = _sync_engine(db_path)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO system_setting (key, value, updated_at) "
                "VALUES ('default_image_backend', 'openai/legacy', CURRENT_TIMESTAMP), "
                "('default_image_backend_t2i', 'openai/t2i', CURRENT_TIMESTAMP), "
                "('default_image_backend_i2i', 'openai/i2i', CURRENT_TIMESTAMP)"
            )
        )

    command.downgrade(cfg, "eedf0aa985e6")

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT key FROM system_setting WHERE key IN "
                "('default_image_backend', 'default_image_backend_t2i', 'default_image_backend_i2i')"
            )
        ).fetchall()
    keys = {r.key for r in rows}
    assert keys == {"default_image_backend"}
