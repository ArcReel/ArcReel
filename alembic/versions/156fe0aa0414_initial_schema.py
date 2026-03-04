"""initial schema

Revision ID: 156fe0aa0414
Revises:
Create Date: 2026-03-04 18:04:19.139135

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '156fe0aa0414'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.String, primary_key=True, nullable=False),
        sa.Column("sdk_session_id", sa.String, nullable=True),
        sa.Column("project_name", sa.String, nullable=False),
        sa.Column("title", sa.String, server_default="", nullable=False),
        sa.Column("status", sa.String, server_default="idle", nullable=False),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
    )
    op.create_index("idx_agent_sessions_status", "agent_sessions", ["status"])
    op.create_index("idx_agent_sessions_project", "agent_sessions", ["project_name", "updated_at"])

    op.create_table(
        "api_calls",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("project_name", sa.String, nullable=False),
        sa.Column("call_type", sa.String, nullable=False),
        sa.Column("model", sa.String, nullable=False),
        sa.Column("prompt", sa.Text, nullable=True),
        sa.Column("resolution", sa.String, nullable=True),
        sa.Column("duration_seconds", sa.Integer, nullable=True),
        sa.Column("aspect_ratio", sa.String, nullable=True),
        sa.Column("generate_audio", sa.Boolean, server_default="1"),
        sa.Column("status", sa.String, server_default="pending", nullable=False),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("output_path", sa.Text, nullable=True),
        sa.Column("started_at", sa.String, nullable=False),
        sa.Column("finished_at", sa.String, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("retry_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Float, server_default="0.0", nullable=False),
        sa.Column("created_at", sa.String, nullable=True),
    )
    op.create_index("idx_api_calls_status", "api_calls", ["status"])
    op.create_index("idx_api_calls_started_at", "api_calls", ["started_at"])
    op.create_index("idx_api_calls_call_type", "api_calls", ["call_type"])
    op.create_index("idx_api_calls_project_name", "api_calls", ["project_name"])

    op.create_table(
        "task_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String, nullable=False),
        sa.Column("project_name", sa.String, nullable=False),
        sa.Column("event_type", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("data_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
    )
    op.create_index("idx_task_events_project_id", "task_events", ["project_name", "id"])

    op.create_table(
        "tasks",
        sa.Column("task_id", sa.String, primary_key=True, nullable=False),
        sa.Column("project_name", sa.String, nullable=False),
        sa.Column("task_type", sa.String, nullable=False),
        sa.Column("media_type", sa.String, nullable=False),
        sa.Column("resource_id", sa.String, nullable=False),
        sa.Column("script_file", sa.String, nullable=True),
        sa.Column("payload_json", sa.Text, nullable=True),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("result_json", sa.Text, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("source", sa.String, server_default="webui", nullable=False),
        sa.Column("dependency_task_id", sa.String, nullable=True),
        sa.Column("dependency_group", sa.String, nullable=True),
        sa.Column("dependency_index", sa.Integer, nullable=True),
        sa.Column("queued_at", sa.String, nullable=False),
        sa.Column("started_at", sa.String, nullable=True),
        sa.Column("finished_at", sa.String, nullable=True),
        sa.Column("updated_at", sa.String, nullable=False),
    )
    op.create_index("idx_tasks_status_queued_at", "tasks", ["status", "queued_at"])
    op.create_index("idx_tasks_project_updated_at", "tasks", ["project_name", "updated_at"])
    op.create_index("idx_tasks_dependency_task_id", "tasks", ["dependency_task_id"])
    op.create_index(
        "idx_tasks_dedupe_active",
        "tasks",
        ["project_name", "task_type", "resource_id", sa.text("COALESCE(script_file, '')")],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
        sqlite_where=sa.text("status IN ('queued', 'running')"),
    )

    op.create_table(
        "worker_lease",
        sa.Column("name", sa.String, primary_key=True, nullable=False),
        sa.Column("owner_id", sa.String, nullable=False),
        sa.Column("lease_until", sa.Float, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("worker_lease")
    op.drop_index("idx_tasks_dedupe_active", table_name="tasks")
    op.drop_index("idx_tasks_dependency_task_id", table_name="tasks")
    op.drop_index("idx_tasks_project_updated_at", table_name="tasks")
    op.drop_index("idx_tasks_status_queued_at", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("idx_task_events_project_id", table_name="task_events")
    op.drop_table("task_events")
    op.drop_index("idx_api_calls_project_name", table_name="api_calls")
    op.drop_index("idx_api_calls_call_type", table_name="api_calls")
    op.drop_index("idx_api_calls_started_at", table_name="api_calls")
    op.drop_index("idx_api_calls_status", table_name="api_calls")
    op.drop_table("api_calls")
    op.drop_index("idx_agent_sessions_project", table_name="agent_sessions")
    op.drop_index("idx_agent_sessions_status", table_name="agent_sessions")
    op.drop_table("agent_sessions")
