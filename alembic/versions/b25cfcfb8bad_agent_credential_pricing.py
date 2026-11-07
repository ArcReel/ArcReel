"""Agent credentials gain multipliers and tier-entry bindings

Revision ID: b25cfcfb8bad
Revises: c7e2a9d4b6f1
Create Date: 2026-10-15 20:00:00.000000

Agent sessions are priced per credential (docs/adr/0102):

- ``multiplier``: multiplies reference cost for each Agent call on this credential; existing value 1.
  At 0, record zero cost without price lookup (docs/adr/0100).
- ``tier_bindings``: maps tiers (``model`` / ``haiku`` / ``sonnet`` / ``opus`` / ``subagent``) to entry
  bindings (model vendor/official model name). Existing values are empty; all tiers match entries by name.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b25cfcfb8bad"
down_revision: str | Sequence[str] | None = "c7e2a9d4b6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_anthropic_credentials", schema=None) as batch_op:
        batch_op.add_column(sa.Column("multiplier", sa.Float(), nullable=False, server_default=sa.text("1")))
        batch_op.add_column(sa.Column("tier_bindings", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_anthropic_credentials", schema=None) as batch_op:
        batch_op.drop_column("tier_bindings")
        batch_op.drop_column("multiplier")
