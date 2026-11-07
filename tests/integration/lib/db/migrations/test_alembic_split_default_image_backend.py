"""Bidirectional Alembic split_default_image_backend_setting migration tests."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[5]

# Cover only splitting and stop at that revision; later revisions collapse both buckets back into defaults.
_SPLIT_REVISION = "5b87accc10dd"


def _sync_engine(db_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{db_path}")


def test_upgrade_copies_legacy_setting_to_two_new_keys(alembic_cfg):
    """Write old default_image_backend, migrate, and verify two new keys with equal values."""
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

    command.upgrade(cfg, _SPLIT_REVISION)

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
    """With old default_image_backend and _t2i already present, migration must not overwrite _t2i."""
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

    command.upgrade(cfg, _SPLIT_REVISION)

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT key, value FROM system_setting WHERE key IN "
                "('default_image_backend_t2i', 'default_image_backend_i2i')"
            )
        ).fetchall()
    settings = {r.key: r.value for r in rows}
    # Existing _t2i is not overwritten.
    assert settings.get("default_image_backend_t2i") == "openai/already-set"
    # Missing _i2i is filled from legacy values.
    assert settings.get("default_image_backend_i2i") == "openai/legacy"


def test_upgrade_no_op_when_no_legacy(alembic_cfg):
    """Without old default_image_backend, migration has no side effects."""
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, _SPLIT_REVISION)

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
    command.upgrade(cfg, _SPLIT_REVISION)

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
