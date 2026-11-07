"""split task provider_endpoint (protocol id) from submitted_base_url (request domain)

`tasks.provider_endpoint` holds only custom-provider protocol identifiers; request domains always belong
in `tasks.submitted_base_url`. Backfill existing values by meaning: http(s) values in `provider_endpoint`
are domains and move to `submitted_base_url`.

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

# This column holds only protocol IDs or request domains; only the latter have http(s) prefixes. IDs come from
# a closed endpoint registry (scheme-free slugs). Domains cannot lack schemes: persistence follows successful
# provider-task HTTP creation, and httpx rejects scheme-free base_url values before sending requests, so they never
# reach persistence. The http(s) prefix is therefore a complete discriminator.
# lower(...) LIKE has identical SQLite/PostgreSQL semantics; no dialect branch needed.
_HAS_DOMAIN = (
    "provider_endpoint IS NOT NULL "
    "AND (lower(provider_endpoint) LIKE 'http://%' OR lower(provider_endpoint) LIKE 'https://%')"
)

# Move before clearing, filling only empty columns: an existing dedicated-domain value wins without overwrite.
_MOVE_DOMAIN = (
    f"UPDATE tasks SET submitted_base_url = provider_endpoint WHERE submitted_base_url IS NULL AND {_HAS_DOMAIN}"
)
_CLEAR_DOMAIN = f"UPDATE tasks SET provider_endpoint = NULL WHERE {_HAS_DOMAIN}"

# Validate with a deliberately independent predicate: reusing backfill criteria always finds zero and proves nothing.
# Instead check for remaining scheme separators, verifying http(s) cleanup and detecting unexpected lower()/LIKE behavior
# or other historical schemes. Protocol IDs never contain `://`; any match means incomplete backfill.
_COUNT_SCHEME_LIKE = "SELECT COUNT(*) FROM tasks WHERE provider_endpoint LIKE '%://%'"

# Built-in provider rows now have only the domain column; restore them on downgrade. Custom rows retain both, unchanged.
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
