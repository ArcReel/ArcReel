"""convert MySQL VARCHAR timestamp columns to DATETIME(6).

Revision ID: bcaaa615ff38
Revises: 7a8b9c0d1e2f
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bcaaa615ff38"
down_revision: str | Sequence[str] | None = "7a8b9c0d1e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# MySQL VARCHAR→DATETIME conversion (mirrors TIMESTAMP_COLUMNS from b942e8c5d545).
_TIMESTAMP_TABLES: dict[str, list[str]] = {
    # task_events 已在 3649100774fa 删除，不在此列。
    "tasks": ["queued_at", "started_at", "finished_at", "updated_at"],
    "worker_lease": ["updated_at"],
    "api_calls": ["started_at", "finished_at", "created_at"],
    "agent_sessions": ["created_at", "updated_at"],
}

_NULLABLE_TIMESTAMPS: set[tuple[str, str]] = {
    ("tasks", "started_at"),
    ("tasks", "finished_at"),
    ("api_calls", "finished_at"),
    ("api_calls", "created_at"),
}


def _convert_mysql_timestamps_to_datetime() -> None:
    """Convert MySQL VARCHAR timestamps → DATETIME(6).

    Migration ``b942e8c5d545`` only ran this conversion for PostgreSQL.  On
    MySQL the columns stayed as ``VARCHAR(64)`` which caused three classes of
    silent misbehavior:

    1. ``datetime.datetime`` written via pymysql escapes tzinfo and writes a
       naive local-time string.  Mixing aware vs naive stored values gave
       wrong comparisons / TypeErrors vs ``utc_now()`` aware inputs.
    2. Reading back returned ``str`` not ``datetime``.
    3. JSON serializers sometimes dropped the ``Z`` / ``+00:00`` suffix so
       REST clients misinterpreted the value as local time.

    MySQL has no ``TIMESTAMP WITH TIME ZONE``; ``DATETIME(6)`` with the ORM
    convention of always writing UTC via ``utc_now()`` preserves cross-
    dialect semantics.  Steps:

    1. Rewrite the stored ISO-8601 strings ("YYYY-MM-DDTHH:MM:SS[.ffffff]Z",
       "" / whitespace) into a MySQL-CAST-compatible format
       "YYYY-MM-DD HH:MM:SS[.ffffff]" and NULLify empties.
    2. ``MODIFY COLUMN`` to ``DATETIME(6)``; MySQL's column-type-change does
       an implicit row-wise CAST which now succeeds.
    3. On downgrade, reverse it: rewrite back to ``YYYY-MM-DDTHH:MM:SS[.ffffff]Z``
       in ``VARCHAR(64)`` to match the pre-migration on-disk format.
    """
    from sqlalchemy.dialects import mysql as _mysql_dialect

    from lib.db.migration_compat import is_mysql

    if not is_mysql():
        return

    bind = op.get_bind()

    # Step 1: normalize textual values.  Handle trailing Z, T separator, and
    # empty strings.  CHAR_LENGTH(TRIM(...)) = 0 catches any whitespace-only.
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
    # keep microsecond precision (matching PG/ORM usage, default `DateTime`
    # on MySQL is DATETIME with 0 frac seconds — rounding would lose info).
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
    for table, columns in _TIMESTAMP_TABLES.items():
        for col in columns:
            nullable = (table, col) in _NULLABLE_TIMESTAMPS
            # 1. MODIFY back to VARCHAR(64); MySQL CASTs DATETIME(6) →
            #    "YYYY-MM-DD HH:MM:SS[.ffffff]" string representation.
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column(
                    col,
                    existing_type=sa.DateTime(timezone=True),
                    type_=sa.String(64),
                    existing_nullable=nullable,
                    existing_server_default=None,
                )
            # 2. Re-format as ISO-8601 with Z.
            stmt = sa.text(
                f"UPDATE `{table}` SET `{col}` = "
                f"CASE "
                f"  WHEN `{col}` IS NULL OR CHAR_LENGTH(TRIM(`{col}`)) = 0 THEN NULL "
                f"  ELSE CONCAT(REPLACE(`{col}`, ' ', 'T'), 'Z') "
                f"END"
            )
            bind.execute(stmt)


def upgrade() -> None:
    """Apply the MySQL timestamp conversion (no-op on SQLite/PG)."""
    _convert_mysql_timestamps_to_datetime()


def downgrade() -> None:
    """Revert MySQL DATETIME(6) columns back to VARCHAR(64) ISO strings."""
    _revert_mysql_timestamps_to_varchar()
