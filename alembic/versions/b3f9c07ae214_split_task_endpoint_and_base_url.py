"""split task provider_endpoint (protocol id) from submitted_base_url (request domain)

`tasks.provider_endpoint` 只承载自定义供应商的协议标识，请求域名一律落
`tasks.submitted_base_url`。存量行按真实语义回填：`provider_endpoint` 中 http(s) 形态的值是
域名，搬去 `submitted_base_url`。

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

_HAS_DOMAIN = (
    "provider_endpoint IS NOT NULL "
    "AND (lower(provider_endpoint) LIKE 'http://%' OR lower(provider_endpoint) LIKE 'https://%')"
)

_MOVE_DOMAIN = (
    f"UPDATE tasks SET submitted_base_url = provider_endpoint WHERE submitted_base_url IS NULL AND {_HAS_DOMAIN}"
)
_CLEAR_DOMAIN = f"UPDATE tasks SET provider_endpoint = NULL WHERE {_HAS_DOMAIN}"

_COUNT_SCHEME_LIKE = "SELECT COUNT(*) FROM tasks WHERE provider_endpoint LIKE '%://%'"

_RESTORE_DOMAIN = (
    "UPDATE tasks SET provider_endpoint = submitted_base_url, submitted_base_url = NULL "
    "WHERE provider_endpoint IS NULL AND submitted_base_url IS NOT NULL"
)


def upgrade() -> None:
    """Backfill data: move request domains out of provider_endpoint into submitted_base_url."""
    bind = op.get_bind()
    bind.execute(sa.text(_MOVE_DOMAIN))
    bind.execute(sa.text(_CLEAR_DOMAIN))
    remaining = bind.execute(sa.text(_COUNT_SCHEME_LIKE)).scalar()
    if remaining:
        raise RuntimeError(f"{remaining} 行的 tasks.provider_endpoint 仍存放请求域名，回填未完成")


def downgrade() -> None:
    """Move builtin-provider domains back into provider_endpoint."""
    bind = op.get_bind()
    bind.execute(sa.text(_RESTORE_DOMAIN))
