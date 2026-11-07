"""Alembic: backfill cloud account identity on provider material links, switching selected uniqueness to identity; downgrade restores account-record IDs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from lib.asset_library.bindings import account_identity

_NOW = "2026-11-03 00:00:00"


def _insert_account(conn: sa.Connection, account_id: int, access_key_id: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO asset_library_account "
            "(id, site, name, region, project_name, access_key_id, secret_access_key, created_at, updated_at) "
            "VALUES (:id, 'volcengine', :name, 'cn-beijing', 'film', :ak, 'TEST-SECRET', :now, :now)"
        ),
        {"id": account_id, "name": f"TEST-{account_id}", "ak": access_key_id, "now": _NOW},
    )


def _insert_link(conn: sa.Connection, link_id: int, *, account_id: int, project: str = "film") -> None:
    conn.execute(
        sa.text(
            "INSERT INTO provider_asset_link "
            "(id, project_name, content_version_id, content_digest, resource_type, resource_id, version, "
            "locator_path, origin, account_id, site, region, provider_project_name, state, submit_name, "
            "selected, check_attempts, created_at, updated_at) "
            "VALUES (:id, 'demo', :cv, :digest, 'characters', 'Alice', 1, 'versions/a.png', 'upload', :account_id, "
            "'volcengine', 'cn-beijing', :project, 'active', 'TEST-name', 1, 0, :now, :now)"
        ),
        {
            "id": link_id,
            "cv": f"cv-{link_id}",
            "digest": "0" * 64,
            "account_id": account_id,
            "project": project,
            "now": _NOW,
        },
    )


def test_upgrade_backfills_identity_from_matching_accounts_only(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
) -> None:
    revision, parent = migration_revisions("*_provider_asset_link_account_identity.py")
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, parent)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            _insert_account(conn, 1, "TEST-ACCOUNT-A")
            _insert_link(conn, 10, account_id=1)
            _insert_link(conn, 11, account_id=1, project="other")  # Account changed to another project.
            _insert_link(conn, 12, account_id=9)  # Account deleted.

        command.upgrade(cfg, revision)

        with engine.begin() as conn:
            rows = conn.execute(sa.text("SELECT id, account_identity FROM provider_asset_link")).fetchall()
            identities = {row.id: row.account_identity for row in rows}
            table_sql = conn.execute(
                sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'provider_asset_link'")
            ).scalar_one()
        indexes = {ix["name"]: ix for ix in sa.inspect(engine).get_indexes("provider_asset_link")}

        command.downgrade(cfg, parent)
        with engine.begin() as conn:
            columns = {c["name"] for c in sa.inspect(conn).get_columns("provider_asset_link")}
    finally:
        engine.dispose()

    # Backfill must match runtime algorithms or upgraded existing links stop being selected.
    assert identities == {
        10: account_identity("volcengine", "TEST-ACCOUNT-A"),
        11: "legacy-unresolved-11",
        12: "legacy-unresolved-12",
    }
    assert "AUTOINCREMENT" in table_sql
    assert "account_identity" in indexes["uq_provider_asset_link_selected"]["column_names"]
    assert "account_id" not in indexes["uq_provider_asset_link_selected"]["column_names"]
    assert "account_identity" not in columns
