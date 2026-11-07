"""drop task_events table

Revision ID: 3649100774fa
Revises: b41d7c5e9a02
Create Date: 2026-07-28 15:08:39.065681

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3649100774fa"
down_revision: str | Sequence[str] | None = "b41d7c5e9a02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """`/tasks/stream` is removed; task_events is write-only, and short-lived historical events are discarded."""
    op.drop_index("idx_task_events_project_id", table_name="task_events")
    op.drop_table("task_events")


def downgrade() -> None:
    """Rebuild schema, including the named FK to tasks; historical events cannot be recovered.

    FK must match the creation migration (``fk_task_events_task_id``), or an earlier downgrade cannot
    find it during drop_constraint and the entire downward migration chain breaks here.
    """
    op.create_table(
        "task_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("project_name", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("data_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.task_id"],
            name="fk_task_events_task_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index("idx_task_events_project_id", "task_events", ["project_name", "id"])
