"""Alembic 迁移：custom_provider 在 SQLite 上启用 AUTOINCREMENT 的 upgrade / downgrade。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_TS = "'2026-10-08 00:00:00'"


@pytest.fixture
def autoincrement_revisions(migration_revisions: Callable[[str], tuple[str, str]]) -> tuple[str, str]:
    return migration_revisions("*_enable_autoincrement_on_custom_provider.py")


def _insert_provider(conn: sa.Connection, name: str, provider_id: int | None = None) -> int:
    id_col, id_val = ("id, ", f"{provider_id}, ") if provider_id is not None else ("", "")
    result = conn.execute(
        sa.text(
            f"INSERT INTO custom_provider ({id_col}display_name, discovery_format, base_url, api_key, "
            "image_max_workers, created_at, updated_at) "
            f"VALUES ({id_val}:name, 'openai', 'https://x', 'k', 2, {_TS}, {_TS})"
        ),
        {"name": name},
    )
    return provider_id if provider_id is not None else int(result.lastrowid)


def _insert_model(conn: sa.Connection, provider_id: int, model_id: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO custom_provider_model "
            "(provider_id, model_id, display_name, endpoint, is_default, is_enabled, created_at, updated_at) "
            f"VALUES (:pid, :mid, :mid, 'openai-chat', 1, 1, {_TS}, {_TS})"
        ),
        {"pid": provider_id, "mid": model_id},
    )


def _seed(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        for pid, name in ((1, "A"), (2, "B"), (3, "C")):
            _insert_provider(conn, name, pid)
        _insert_model(conn, 2, "m-b")
        _insert_model(conn, 3, "m-c")


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    with engine.begin() as conn:
        return {
            "providers": conn.execute(
                sa.text("SELECT id, display_name, image_max_workers FROM custom_provider ORDER BY id")
            ).fetchall(),
            "models": conn.execute(
                sa.text("SELECT provider_id, model_id FROM custom_provider_model ORDER BY provider_id")
            ).fetchall(),
        }


def _schema(engine: sa.Engine) -> dict[str, object]:
    """引用方与本表的外键、索引、约束：升降级前后都应保持不变。"""
    insp = sa.inspect(engine)
    return {
        "model_fks": insp.get_foreign_keys("custom_provider_model"),
        "model_indexes": insp.get_indexes("custom_provider_model"),
        "model_uniques": insp.get_unique_constraints("custom_provider_model"),
        "provider_checks": sorted(c["name"] for c in insp.get_check_constraints("custom_provider")),
        "provider_pk": insp.get_pk_constraint("custom_provider")["constrained_columns"],
    }


def _new_id_after_deleting_max(engine: sa.Engine) -> tuple[int, int]:
    with engine.begin() as conn:
        max_id = conn.execute(sa.text("SELECT MAX(id) FROM custom_provider")).scalar_one()
        conn.execute(sa.text("DELETE FROM custom_provider_model WHERE provider_id = :id"), {"id": max_id})
        conn.execute(sa.text("DELETE FROM custom_provider WHERE id = :id"), {"id": max_id})
        new_id = _insert_provider(conn, "new")
    return max_id, new_id


def test_upgrade_keeps_data_and_stops_reusing_deleted_max_id(
    alembic_cfg: tuple[Config, Path], autoincrement_revisions: tuple[str, str]
):
    revision_id, parent_id = autoincrement_revisions
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, parent_id)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        _seed(engine)
        before_data, before_schema = _snapshot(engine), _schema(engine)

        command.upgrade(cfg, revision_id)

        assert _snapshot(engine) == before_data
        assert _schema(engine) == before_schema
        deleted_id, new_id = _new_id_after_deleting_max(engine)
        assert new_id != deleted_id
    finally:
        engine.dispose()


def test_downgrade_keeps_data_and_schema(alembic_cfg: tuple[Config, Path], autoincrement_revisions: tuple[str, str]):
    revision_id, parent_id = autoincrement_revisions
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, revision_id)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        _seed(engine)
        before_data, before_schema = _snapshot(engine), _schema(engine)

        command.downgrade(cfg, parent_id)

        assert _snapshot(engine) == before_data
        assert _schema(engine) == before_schema
        with engine.begin() as conn:
            ddl = conn.execute(
                sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'custom_provider'")
            ).scalar_one()
        assert "AUTOINCREMENT" not in ddl.upper()
    finally:
        engine.dispose()
