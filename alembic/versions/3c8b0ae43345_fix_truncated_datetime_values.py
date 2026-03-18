"""fix_truncated_datetime_values

A previous migration (b942e8c5d545) used batch_alter_table to change
timestamp columns from String to DateTime(timezone=True) on SQLite.
Alembic's batch mode uses CAST(col AS DATETIME) during the data copy,
and SQLite's DATETIME has NUMERIC affinity — so ISO strings like
"2026-03-17T14:37:11+00:00" were truncated to the integer 2026.

This migration converts those corrupted integer values back to a
placeholder datetime string so the application can read them again.

Revision ID: 3c8b0ae43345
Revises: dd7c8cc633e3
Create Date: 2026-03-18 16:22:16.940767

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3c8b0ae43345"
down_revision: Union[str, Sequence[str], None] = "6cb95261c2b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables and their datetime columns that were corrupted by the CAST bug.
TIMESTAMP_COLUMNS: dict[str, list[str]] = {
    "tasks": ["queued_at", "started_at", "finished_at", "updated_at"],
    "task_events": ["created_at"],
    "worker_lease": ["updated_at"],
    "api_calls": ["started_at", "finished_at", "created_at"],
    "agent_sessions": ["created_at", "updated_at"],
}

# Placeholder: original timestamps are lost; use epoch so they sort first
# and are obviously synthetic.
PLACEHOLDER = "1970-01-01T00:00:00+00:00"


def upgrade() -> None:
    """Convert truncated integer timestamps back to valid ISO strings."""
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        # Only SQLite was affected by the CAST bug.
        return

    for table, columns in TIMESTAMP_COLUMNS.items():
        for col in columns:
            # typeof() returns 'integer' for the corrupted rows
            op.execute(
                sa.text(
                    f"UPDATE {table} SET {col} = '{PLACEHOLDER}' "
                    f"WHERE typeof({col}) = 'integer'"
                )
            )


def downgrade() -> None:
    """No-op: we cannot restore the original (lost) timestamps."""
    pass
