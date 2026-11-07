"""rebuild session event log for typed skill and subagent entries

Revision ID: 3b841838ac16
Revises: f3d21ac90b17
Create Date: 2026-07-06 16:55:43.613251

The event log is an SDK-transcript materialized view; deleting it loses no source truth. As write-time
shaping evolves (skill entries retain only names/arguments, lazy generation merges subagent subpaths),
clear all existing entries built by old rules; first session access lazily rebuilds them from transcripts.

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3b841838ac16"
down_revision: str | Sequence[str] | None = "f3d21ac90b17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(sa.text("DELETE FROM agent_session_event_log"))


def downgrade() -> None:
    """Downgrade schema."""
    # Irreversible data migration; old code also rebuilds lazily on demand, so no backfill needed.
