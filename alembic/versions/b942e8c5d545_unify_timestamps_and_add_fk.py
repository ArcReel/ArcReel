"""unify_timestamps_and_add_fk

Revision ID: b942e8c5d545
Revises: ecbb53758daa
Create Date: 2026-03-17 14:37:11.783399

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b942e8c5d545"
down_revision: Union[str, Sequence[str], None] = "ecbb53758daa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIMESTAMP_COLUMNS: dict[str, list[str]] = {
    "tasks": ["queued_at", "started_at", "finished_at", "updated_at"],
    "task_events": ["created_at"],
    "worker_lease": ["updated_at"],
    "api_calls": ["started_at", "finished_at", "created_at"],
    "agent_sessions": ["created_at", "updated_at"],
}

NULLABLE_COLUMNS: set[tuple[str, str]] = {
    ("tasks", "started_at"),
    ("tasks", "finished_at"),
    ("api_calls", "finished_at"),
    ("api_calls", "created_at"),
}


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # 1. Clean orphan task_events before adding FK constraint
    op.execute(
        sa.text(
            "DELETE FROM task_events "
            "WHERE task_id NOT IN (SELECT task_id FROM tasks)"
        )
    )

    # 2. Convert String timestamp columns → DateTime(timezone=True)
    #    SQLite: skip column type change — SQLAlchemy's DateTime type processor
    #    handles str↔datetime conversion at the Python level regardless of the
    #    underlying column DDL type.  Alembic's batch_alter_table on SQLite uses
    #    CAST(col AS DATETIME) during data copy, and SQLite's DATETIME has
    #    NUMERIC affinity, which truncates ISO strings like "2026-03-17T..."
    #    to the integer 2026.
    if is_pg:
        for table, columns in TIMESTAMP_COLUMNS.items():
            for col in columns:
                op.execute(
                    sa.text(
                        f"ALTER TABLE {table} ALTER COLUMN {col} "
                        f"TYPE TIMESTAMP WITH TIME ZONE "
                        f"USING CASE WHEN {col} IS NOT NULL AND {col} != '' "
                        f"THEN {col}::timestamptz END"
                    )
                )

    # 2b. Rebuild expression-based partial index lost during batch rebuild
    #     (SQLAlchemy cannot reflect expression indexes on SQLite)
    if not is_pg:
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_dedupe_active "
                "ON tasks(project_name, task_type, resource_id, COALESCE(script_file, '')) "
                "WHERE status IN ('queued', 'running')"
            )
        )

    # 3. Add FK constraint: task_events.task_id → tasks.task_id
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.create_foreign_key(
            "fk_task_events_task_id",
            "tasks",
            ["task_id"],
            ["task_id"],
            ondelete="CASCADE",
        )

    # 4. Fix server_default for api_calls.generate_audio ("1" → true)
    with op.batch_alter_table("api_calls") as batch_op:
        batch_op.alter_column("generate_audio", server_default=sa.true())


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # 1. Remove FK constraint
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.drop_constraint("fk_task_events_task_id", type_="foreignkey")

    # 2. Revert server_default
    with op.batch_alter_table("api_calls") as batch_op:
        batch_op.alter_column("generate_audio", server_default="1")

    # 3. Revert DateTime(timezone=True) → String
    #    SQLite: no-op (column type was never changed; see upgrade comment).
    if is_pg:
        for table, columns in TIMESTAMP_COLUMNS.items():
            for col in columns:
                op.execute(
                    sa.text(
                        f"ALTER TABLE {table} ALTER COLUMN {col} "
                        f"TYPE VARCHAR "
                        f"USING to_char({col} AT TIME ZONE 'UTC', "
                        f"'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
                    )
                )
