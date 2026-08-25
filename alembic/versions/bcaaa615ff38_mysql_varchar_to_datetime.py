"""convert MySQL VARCHAR timestamp columns to DATETIME(6).

Revision ID: bcaaa615ff38
Revises: 7a8b9c0d1e2f
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql as _mysql_dialect

from alembic import op

revision: str = "bcaaa615ff38"
down_revision: str | Sequence[str] | None = "7a8b9c0d1e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables with timestamp columns that need conversion
_TIMESTAMP_TABLES: dict[str, list[str]] = {
    "agent_sessions": ["created_at", "updated_at"],
    "agent_session_entries": ["created_at"],
    "agent_session_summaries": ["created_at"],
    "api_calls": ["created_at"],
    "tasks": ["created_at", "updated_at", "scheduled_at", "started_at", "completed_at"],
    "assets": ["created_at"],
    "worker_lease": ["acquired_at", "expires_at"],
}

# Columns that were originally nullable (VARCHAR NULL)
_NULLABLE_TIMESTAMPS = {
    ("tasks", "scheduled_at"),
    ("tasks", "started_at"),
    ("tasks", "completed_at"),
}


def _convert_mysql_timestamps_to_datetime() -> None:
    """Convert VARCHAR timestamp columns to DATETIME(6) for MySQL."""
    from lib.db.migration_compat import is_mysql

    if not is_mysql():
        return

    bind = op.get_bind()

    # Step 0: Set session timezone to UTC to ensure consistent conversion.
    bind.execute(sa.text("SET SESSION time_zone = '+00:00'"))

    # Step 1: normalize textual values. Handle trailing Z, T separator, and
    # empty strings. CHAR_LENGTH(TRIM(...)) = 0 catches any whitespace-only.
    for table, columns in _TIMESTAMP_TABLES.items():
        for col in columns:
            stmt = sa.text(
                f"UPDATE `{table}` SET `{col}` = "
                f"CASE "
                f"  WHEN `{col}` IS NULL OR CHAR_LENGTH(TRIM(`{col}`)) = 0 THEN NULL "
                f"  WHEN `{col}` LIKE '%Z' THEN "
                f"    REPLACE(LEFT(`{col}`, CHAR_LENGTH(`{col}`) - 1), 'T', ' ') "
                f"  WHEN `{col}` LIKE '%T%' THEN REPLACE(`{col}`, 'T', ' ') "
                f"  ELSE `{col}` "
                f"END"
            )
            bind.execute(stmt)

    # Step 2: ALTER the column type using MySQL-specific DATETIME(fsp=6) so we
    # keep microsecond precision.
    for table, columns in _TIMESTAMP_TABLES.items():
        for col in columns:
            nullable = (table, col) in _NULLABLE_TIMESTAMPS
            dtype = _mysql_dialect.DATETIME(fsp=6)
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column(
                    col,
                    existing_type=sa.String(64),
                    type_=dtype,
                    existing_nullable=nullable,
                    existing_server_default=None,
                )


def _revert_mysql_timestamps_to_varchar() -> None:
    """Downgrade mirror for _convert_mysql_timestamps_to_datetime."""
    from lib.db.migration_compat import is_mysql

    if not is_mysql():
        return

    bind = op.get_bind()

    # Step 0: Reset session timezone to UTC for downgrade consistency.
    bind.execute(sa.text("SET SESSION time_zone = '+00:00'"))

    for table, columns in _TIMESTAMP_TABLES.items():
        for col in columns:
            nullable = (table, col) in _NULLABLE_TIMESTAMPS
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column(
                    col,
                    existing_type=_mysql_dialect.DATETIME(fsp=6),
                    type_=sa.String(64),
                    existing_nullable=nullable,
                    existing_server_default=None,
                )

            # Restore original string values from DATETIME format
            stmt = sa.text(
                f"UPDATE `{table}` SET `{col}` = "
                f"CASE "
                f"  WHEN `{col}` IS NOT NULL THEN "
                f"    CONCAT(DATE_FORMAT(`{col}`, '%Y-%m-%d %H:%i:%s'), 'Z') "
                f"  ELSE NULL "
                f"END"
            )
            bind.execute(stmt)


def upgrade() -> None:
    _convert_mysql_timestamps_to_datetime()


def downgrade() -> None:
    _revert_mysql_timestamps_to_varchar()