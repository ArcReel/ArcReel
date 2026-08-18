"""split task provider_endpoint (protocol id) from submitted_base_url (request domain)

`tasks.provider_endpoint` 收窄为只承载自定义供应商的协议标识；内置供应商此前落在该列的
请求域名迁入 `tasks.submitted_base_url`。存量行按真实语义回填：值是 http(s) 形态的即域名。

Revision ID: b3f9c07ae214
Revises: f6a41746c0de
Create Date: 2026-08-18 04:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3f9c07ae214"
down_revision: str | Sequence[str] | None = "f6a41746c0de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 域名判据：该列只可能出现协议标识或请求域名，只有 http(s) 前缀的是后者。
# lower(...) LIKE 在 SQLite 与 PostgreSQL 上语义一致，无需方言分支。
_HAS_DOMAIN = (
    "provider_endpoint IS NOT NULL "
    "AND (lower(provider_endpoint) LIKE 'http://%' OR lower(provider_endpoint) LIKE 'https://%')"
)

# 先搬后清，且只填空列：域名两处都有值的行以专列为准，不覆盖。
_MOVE_DOMAIN = (
    f"UPDATE tasks SET submitted_base_url = provider_endpoint WHERE submitted_base_url IS NULL AND {_HAS_DOMAIN}"
)
_CLEAR_DOMAIN = f"UPDATE tasks SET provider_endpoint = NULL WHERE {_HAS_DOMAIN}"
_COUNT_DOMAIN = f"SELECT COUNT(*) FROM tasks WHERE {_HAS_DOMAIN}"

# 回填后内置供应商的行只剩域名一列有值，据此反向还原；自定义供应商的行两列俱在，不动。
_RESTORE_DOMAIN = (
    "UPDATE tasks SET provider_endpoint = submitted_base_url, submitted_base_url = NULL "
    "WHERE provider_endpoint IS NULL AND submitted_base_url IS NOT NULL"
)


def upgrade() -> None:
    """Backfill data: move request domains out of provider_endpoint into submitted_base_url."""
    bind = op.get_bind()
    bind.execute(sa.text(_MOVE_DOMAIN))
    bind.execute(sa.text(_CLEAR_DOMAIN))
    remaining = bind.execute(sa.text(_COUNT_DOMAIN)).scalar()
    if remaining:
        raise RuntimeError(f"{remaining} 行的 tasks.provider_endpoint 仍存放请求域名，回填未完成")


def downgrade() -> None:
    """Move builtin-provider domains back into provider_endpoint."""
    bind = op.get_bind()
    bind.execute(sa.text(_RESTORE_DOMAIN))
