"""normalize agent credential base_url

Revision ID: c9fcbd98e62c
Revises: 9f3c7a52d1b4
Create Date: 2026-09-08 01:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9fcbd98e62c"
down_revision: str | Sequence[str] | None = "9f3c7a52d1b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MESSAGES_PATH = "/v1/messages"


def _normalize(raw: str) -> str:
    """Frozen copy of ``lib.config.url_utils.normalize_anthropic_base_url`` rules.

    Strip surrounding whitespace and trailing slashes, then remove an exact ``/v1/messages`` suffix
    and strip trailing slashes again. Query / fragment / userinfo values receive only these three steps;
    connection-test entry validation rejects them.
    """
    stripped = raw.strip().rstrip("/")
    if stripped.endswith(_MESSAGES_PATH):
        stripped = stripped[: -len(_MESSAGES_PATH)].rstrip("/")
    return stripped


def upgrade() -> None:
    """Normalize existing Agent credential base_url so stored values equal invoked values.

    Runtime passes base_url unchanged to ``ANTHROPIC_BASE_URL``; Claude CLI appends ``/v1/messages``.
    Old versions stored unnormalized ``/v1/messages`` suffixes/trailing slashes, diverging connection tests from runtime.
    Change only rows whose normalized value differs.
    """
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, base_url FROM agent_anthropic_credentials")).fetchall()
    for row in rows:
        raw = row.base_url or ""
        normalized = _normalize(raw)
        if normalized == raw:
            continue
        bind.execute(
            sa.text("UPDATE agent_anthropic_credentials SET base_url = :base_url WHERE id = :id"),
            {"base_url": normalized, "id": row.id},
        )


def downgrade() -> None:
    """Normalization is idempotent data cleanup; old versions accept normalized values, so no rollback needed."""
