"""供应商素材关联冻结云账号身份

Revision ID: e3b9c4d27a61
Revises: aefa137b10ad
Create Date: 2026-11-03 21:00:00.000000

素材库账号记录可以原地换成另一个云账号的 Access Key，按账号记录 ID 判断范围会把旧账号的素材 ID 用到新账号。
关联与入库任务改为冻结提交时的云账号身份（站点与 Access Key ID 的摘要），选定唯一键随之以身份代替账号记录 ID。

存量关联按它所记账号的当前 Access Key 补写身份：只有账号仍在、站点、区域与项目都与关联一致时才补写；其余
写入每行各不相同的 ``legacy-unresolved-<id>``，不与任何账号相等，关联保留原范围但不再适用。账号在本迁移
之前就已被换过 Key 的关联无法追溯，按当前 Key 归属。
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
    # 与 lib.asset_library.bindings.account_identity 保持一致；迁移冻结算法，不导入运行时代码。
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

    # 同一云账号若被登记成多条账号记录，旧键允许各有一条选定记录；按身份合并后只保留最新的一条。
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
    # 同一账号记录换 Key 前后可以各有一条选定记录；回到按账号记录 ID 的键时只保留最新的一条。
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
