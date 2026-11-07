"""Freeze cloud-account identity in provider asset links

Revision ID: e3b9c4d27a61
Revises: aefa137b10ad
Create Date: 2026-11-03 21:00:00.000000

An asset-library account record can change to another cloud account’s Access Key in place; record-ID
scoping would reuse old asset IDs for the new account. Links/registration tasks freeze submission-time
cloud identity (digest of site and Access Key ID); selected unique keys replace account record ID with identity.

Backfill existing links from current keys only when the account still exists and site, region, project
match the link. Otherwise use distinct per-row ``legacy-unresolved-<id>`` identities matching no account;
links preserve their old scope but become inapplicable. Prior key changes cannot be traced; attribute
such links to current keys.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e3b9c4d27a61"
down_revision: str | Sequence[str] | None = "aefa137b10ad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "provider_asset_link"
_SELECTED_INDEX = "uq_provider_asset_link_selected"
_SCOPE = ["site", "region", "provider_project_name"]


def _account_identity(site: str, access_key_id: str) -> str:
    # Match lib.asset_library.bindings.account_identity; freeze the algorithm without runtime imports.
    payload = json.dumps(
        {"domain": "arcreel/asset-library-account/v1", "site": site, "access_key_id": access_key_id.strip()},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _create_selected_index(account_column: str) -> None:
    op.create_index(
        _SELECTED_INDEX,
        _TABLE,
        ["project_name", "content_version_id", account_column, *_SCOPE],
        unique=True,
        sqlite_where=sa.text("selected = 1"),
        postgresql_where=sa.text("selected"),
    )


def upgrade() -> None:
    op.drop_index(_SELECTED_INDEX, table_name=_TABLE)
    op.add_column(_TABLE, sa.Column("account_identity", sa.String(length=64), nullable=True))

    conn = op.get_bind()
    accounts = {
        row.id: row
        for row in conn.execute(
            sa.text("SELECT id, site, region, project_name, access_key_id FROM asset_library_account")
        )
    }
    links = conn.execute(
        sa.text(f"SELECT id, account_id, site, region, provider_project_name FROM {_TABLE}")
    ).fetchall()
    for link in links:
        account = accounts.get(link.account_id)
        if (
            account is not None
            and account.site == link.site
            and account.region == link.region
            and account.project_name == link.provider_project_name
        ):
            identity = _account_identity(account.site, account.access_key_id)
        else:
            identity = f"legacy-unresolved-{link.id}"
        conn.execute(
            sa.text(f"UPDATE {_TABLE} SET account_identity = :identity WHERE id = :id"),
            {"identity": identity, "id": link.id},
        )

    with op.batch_alter_table(_TABLE, table_kwargs={"sqlite_autoincrement": True}) as batch_op:
        batch_op.alter_column("account_identity", existing_type=sa.String(length=64), nullable=False)

    # Duplicate records for one cloud account formerly each allowed a selected link; identity merging keeps only the newest.
    conn.execute(
        sa.text(
            f"UPDATE {_TABLE} SET selected = :false, unlinked_at = CURRENT_TIMESTAMP "
            f"WHERE selected = :true AND EXISTS (SELECT 1 FROM {_TABLE} AS newer "
            f"WHERE newer.selected = :true AND newer.id > {_TABLE}.id "
            f"AND newer.project_name = {_TABLE}.project_name "
            f"AND newer.content_version_id = {_TABLE}.content_version_id "
            f"AND newer.account_identity = {_TABLE}.account_identity "
            f"AND newer.site = {_TABLE}.site AND newer.region = {_TABLE}.region "
            f"AND newer.provider_project_name = {_TABLE}.provider_project_name)"
        ),
        {"true": True, "false": False},
    )
    _create_selected_index("account_identity")


def downgrade() -> None:
    op.drop_index(_SELECTED_INDEX, table_name=_TABLE)
    # Key changes on one account record can each have selected links; restoring record-ID keys keeps only the newest.
    conn = op.get_bind()
    conn.execute(
        sa.text(
            f"UPDATE {_TABLE} SET selected = :false, unlinked_at = CURRENT_TIMESTAMP "
            f"WHERE selected = :true AND EXISTS (SELECT 1 FROM {_TABLE} AS newer "
            f"WHERE newer.selected = :true AND newer.id > {_TABLE}.id "
            f"AND newer.project_name = {_TABLE}.project_name "
            f"AND newer.content_version_id = {_TABLE}.content_version_id "
            f"AND newer.account_id = {_TABLE}.account_id "
            f"AND newer.site = {_TABLE}.site AND newer.region = {_TABLE}.region "
            f"AND newer.provider_project_name = {_TABLE}.provider_project_name)"
        ),
        {"true": True, "false": False},
    )
    with op.batch_alter_table(_TABLE, table_kwargs={"sqlite_autoincrement": True}) as batch_op:
        batch_op.drop_column("account_identity")
    _create_selected_index("account_id")
