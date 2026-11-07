"""Alembic: enforce one object-storage connection at DB level, deterministically reconciling premigration duplicates."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_NOW = "2026-11-01 00:00:00"


def _engine(db_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{db_path}")


def _insert_connection(conn: sa.Connection, bucket: str, *, slot: int | None = None) -> int:
    columns = "kind, region, endpoint, bucket, access_key_id, secret_access_key, created_at, updated_at"
    values = "'tos', 'cn-beijing', 'tos-cn-beijing.volces.com', :bucket, 'TEST-AK', 'TEST-SK', :now, :now"
    if slot is not None:
        columns += ", slot"
        values += ", :slot"
    result = conn.execute(
        sa.text(f"INSERT INTO object_storage_connection ({columns}) VALUES ({values})"),
        {"bucket": bucket, "now": _NOW, "slot": slot},
    )
    return int(result.lastrowid)


def _insert_formal_object(conn: sa.Connection, connection_id: int, state: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO object_storage_formal_object (connection_id, endpoint, bucket, object_key, content_digest, "
            "size, state, delete_attempts, created_at, updated_at) VALUES (:cid, 'tos-cn-beijing.volces.com', 'b', "
            ":key, 'd', 0, :state, 0, :now, :now)"
        ),
        {"cid": connection_id, "key": f"arcreel-materials/{connection_id}-{state}", "state": state, "now": _NOW},
    )


def _insert_relay_object(conn: sa.Connection, connection_id: int, state: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO object_storage_relay_object (connection_id, endpoint, bucket, object_key, state, "
            "delete_attempts, created_at, updated_at) VALUES (:cid, 'tos-cn-beijing.volces.com', 'b', 'k', :state, "
            "0, :now, :now)"
        ),
        {"cid": connection_id, "state": state, "now": _NOW},
    )


def test_duplicates_keep_lowest_as_current_and_retire_only_referenced_ones(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions("*_object_storage_connection_singleton.py")
    command.upgrade(cfg, parent)

    engine = _engine(db_path)
    try:
        with engine.begin() as conn:
            current = _insert_connection(conn, "first")
            unreferenced = _insert_connection(conn, "unreferenced")
            only_deleted_refs = _insert_connection(conn, "only-deleted-refs")
            formal_ref = _insert_connection(conn, "formal-ref")
            relay_ref = _insert_connection(conn, "relay-ref")
            _insert_formal_object(conn, only_deleted_refs, "deleted")
            _insert_relay_object(conn, only_deleted_refs, "deleted")
            _insert_formal_object(conn, formal_ref, "stored")
            _insert_relay_object(conn, relay_ref, "retained")

        command.upgrade(cfg, revision)

        with engine.connect() as conn:
            rows = conn.execute(sa.text("SELECT id, bucket, slot FROM object_storage_connection ORDER BY id")).all()
        assert [tuple(row) for row in rows] == [
            (current, "first", 1),
            (formal_ref, "formal-ref", None),
            (relay_ref, "relay-ref", None),
        ]
        assert unreferenced not in {row[0] for row in rows}

        # Unique indexes block a second current connection; deleted connection IDs are not reused.
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
            _insert_connection(conn, "second-current", slot=1)
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM object_storage_connection WHERE id = :id"), {"id": current})
            assert _insert_connection(conn, "next", slot=1) > relay_ref
    finally:
        engine.dispose()

    command.downgrade(cfg, parent)
    engine = _engine(db_path)
    try:
        columns = {column["name"] for column in sa.inspect(engine).get_columns("object_storage_connection")}
        indexes = {index["name"] for index in sa.inspect(engine).get_indexes("object_storage_connection")}
    finally:
        engine.dispose()
    assert "slot" not in columns
    assert "uq_object_storage_connection_slot" not in indexes


def test_single_existing_connection_becomes_current(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions("*_object_storage_connection_singleton.py")
    command.upgrade(cfg, parent)
    engine = _engine(db_path)
    try:
        with engine.begin() as conn:
            only = _insert_connection(conn, "only")
        command.upgrade(cfg, revision)
        with engine.connect() as conn:
            rows = conn.execute(sa.text("SELECT id, slot FROM object_storage_connection")).all()
        assert [tuple(row) for row in rows] == [(only, 1)]
    finally:
        engine.dispose()
