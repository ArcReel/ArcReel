"""Alembic 迁移测试：object_storage_connection 新增收敛回本地持久化状态列（a3e8b1c4d7f2）。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_REVISION_PATTERN = "*_object_storage_relocation_state.py"
_NOW = "2026-11-28 00:00:00"


def test_upgrade_adds_relocation_columns_preserving_existing_rows_and_autoincrement(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
):
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions(_REVISION_PATTERN)
    command.upgrade(cfg, parent)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO object_storage_connection "
                    "(kind, region, endpoint, bucket, addressing_style, needs_endpoint_review, "
                    "access_key_id, secret_access_key, slot, created_at, updated_at) "
                    "VALUES ('s3', 'cn-beijing', 'https://tos-s3-cn-beijing.volces.com', 'old-bkt', "
                    "'virtual', 0, 'AK1', 'SK1', 1, :now, :now)"
                ),
                {"now": _NOW},
            )
            first_id = conn.execute(sa.text("SELECT id FROM object_storage_connection WHERE slot = 1")).scalar_one()

        command.upgrade(cfg, revision)

        with engine.begin() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT id, relocating, relocate_delete_remote, relocate_mode "
                    "FROM object_storage_connection WHERE slot = 1"
                )
            ).one()
            assert row == (first_id, 0, None, None)

            conn.execute(sa.text("DELETE FROM object_storage_connection WHERE id = :id"), {"id": first_id})
            conn.execute(
                sa.text(
                    "INSERT INTO object_storage_connection "
                    "(kind, region, endpoint, bucket, addressing_style, needs_endpoint_review, "
                    "relocating, relocate_delete_remote, relocate_mode, "
                    "access_key_id, secret_access_key, slot, created_at, updated_at) "
                    "VALUES ('s3', 'us-east-1', 'https://s3.us-east-1.amazonaws.com', 'new-bkt', "
                    "'auto', 0, 1, 0, 'switch', 'AK2', 'SK2', 1, :now, :now)"
                ),
                {"now": _NOW},
            )
            second_id = conn.execute(sa.text("SELECT id FROM object_storage_connection WHERE slot = 1")).scalar_one()
            assert second_id > first_id

        command.downgrade(cfg, parent)
        with engine.begin() as conn:
            cols = {col["name"] for col in sa.inspect(conn).get_columns("object_storage_connection")}
            assert "relocating" not in cols
            assert "relocate_delete_remote" not in cols
            assert "relocate_mode" not in cols

        command.upgrade(cfg, revision)
        with engine.begin() as conn:
            cols = {col["name"] for col in sa.inspect(conn).get_columns("object_storage_connection")}
            assert {"relocating", "relocate_delete_remote", "relocate_mode"} <= cols
    finally:
        engine.dispose()
