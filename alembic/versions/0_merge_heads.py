"""merge multiple heads (b3f9c07ae214 + bcaaa615ff38)

Merge two migration branches produced during parallel development:
- b3f9c07ae214: split task provider_endpoint from submitted_base_url
- bcaaa615ff38: convert MySQL VARCHAR timestamps to DATETIME(6)

This revision declares both as down_revision so alembic sees a single head
again. No DDL / data changes — the two branches modify disjoint columns.

Revision ID: 00000000_merge_heads
Revises: b3f9c07ae214, bcaaa615ff38
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "00000000_merge_heads"
down_revision: str | Sequence[str] | None = ("b3f9c07ae214", "bcaaa615ff38")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op: merge marker only. Both parent migrations carry the changes."""
    pass


def downgrade() -> None:
    """No-op: merge marker only."""
    pass