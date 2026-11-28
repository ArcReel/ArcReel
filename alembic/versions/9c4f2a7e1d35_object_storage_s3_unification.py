"""统一对象存储连接为标准 S3 兼容协议并迁移存量活跃火山 TOS 连接

Revision ID: 9c4f2a7e1d35
Revises: 2d4a9f6c8b31
Create Date: 2026-11-28 12:00:00.000000

为 ``object_storage_connection`` 新增 ``addressing_style``（默认 ``auto``）与 ``needs_endpoint_review``（默认
``false``）列；在同一事务内将存量活跃（``slot = 1``）的 ``kind = 'tos'`` 连接升级为 ``kind = 's3'``：

- 符合火山引擎/BytePlus 官方命名规则的公网、内网域名（含历史无 scheme 主机及已是 ``tos-s3-*`` 的域名）自动转换为
  ``<scheme>://tos-s3-<region>.<domain>``，默认设为虚拟主机风格（``virtual``），并同步更新匹配
  ``(connection_id, old_endpoint, bucket)`` 的正式对象与中转副本记录（含上传中与待删记录）；
- 历史非活跃退役连接（``slot IS NULL``）及其引用行保持原样，不按桶名批量认领；
- 非常规自定义域名跳过自动改写，保留原 Endpoint 值与 ``auto`` 寻址风格，置 ``needs_endpoint_review = true``
  供设置页提示核对。

列变更使用原生 ``ALTER TABLE ADD COLUMN``，SQLite 上不重建表以保留 ``AUTOINCREMENT``。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlsplit

import sqlalchemy as sa

from alembic import op

revision: str = "9c4f2a7e1d35"
down_revision: str | Sequence[str] | None = "2d4a9f6c8b31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "object_storage_connection"
_FORMAL_TABLE = "object_storage_formal_object"
_RELAY_TABLE = "object_storage_relay_object"

_NATIVE_TOS_HOST = re.compile(r"^tos-(?!s3-)([a-z0-9-]+)\.(i?volces|i?bytepluses)\.com$")
_S3_TOS_HOST = re.compile(r"^tos-s3-([a-z0-9-]+)\.(i?volces|i?bytepluses)\.com$")


def _convert_legacy_tos_endpoint(raw_endpoint: str) -> tuple[str, str, bool]:
    """返回 ``(new_endpoint, addressing_style, needs_endpoint_review)``。"""
    trimmed = raw_endpoint.strip()
    if not trimmed or any(ord(ch) <= 32 or ord(ch) == 127 for ch in trimmed):
        return raw_endpoint, "auto", True
    candidate = trimmed if "://" in trimmed else f"https://{trimmed}"
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return raw_endpoint, "auto", True
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        return raw_endpoint, "auto", True
    host = parsed.hostname.lower()
    native_match = _NATIVE_TOS_HOST.match(host)
    if native_match is not None:
        s3_host = f"tos-s3-{native_match.group(1)}.{native_match.group(2)}.com"
    elif _S3_TOS_HOST.match(host) is not None:
        s3_host = host
    else:
        return raw_endpoint, "auto", True
    port_suffix = f":{port}" if port is not None else ""
    return f"{parsed.scheme}://{s3_host}{port_suffix}", "virtual", False


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("addressing_style", sa.String(length=16), nullable=False, server_default="auto"))
    op.add_column(_TABLE, sa.Column("needs_endpoint_review", sa.Boolean(), nullable=False, server_default=sa.false()))

    bind = op.get_bind()
    active_rows = bind.execute(
        sa.text(f"SELECT id, endpoint, bucket FROM {_TABLE} WHERE slot = 1 AND kind = 'tos'")
    ).all()
    for conn_id, old_endpoint, bucket in active_rows:
        new_endpoint, addressing_style, needs_review = _convert_legacy_tos_endpoint(old_endpoint)
        bind.execute(
            sa.text(
                f"UPDATE {_TABLE} "
                "SET kind = 's3', endpoint = :new_endpoint, addressing_style = :addressing_style, "
                "needs_endpoint_review = :needs_review "
                "WHERE id = :conn_id"
            ),
            {
                "new_endpoint": new_endpoint,
                "addressing_style": addressing_style,
                "needs_review": needs_review,
                "conn_id": conn_id,
            },
        )
        if new_endpoint != old_endpoint:
            params = {
                "new_endpoint": new_endpoint,
                "conn_id": conn_id,
                "old_endpoint": old_endpoint,
                "bucket": bucket,
            }
            bind.execute(
                sa.text(
                    f"UPDATE {_FORMAL_TABLE} "
                    "SET endpoint = :new_endpoint "
                    "WHERE connection_id = :conn_id AND endpoint = :old_endpoint AND bucket = :bucket"
                ),
                params,
            )
            bind.execute(
                sa.text(
                    f"UPDATE {_RELAY_TABLE} "
                    "SET endpoint = :new_endpoint "
                    "WHERE connection_id = :conn_id AND endpoint = :old_endpoint AND bucket = :bucket"
                ),
                params,
            )


def downgrade() -> None:
    """移除新增列；已由 TOS 升级为 S3 的连接数据保持不变。"""
    op.drop_column(_TABLE, "needs_endpoint_review")
    op.drop_column(_TABLE, "addressing_style")
