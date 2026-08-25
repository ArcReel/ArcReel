"""add durable generation batches

Revision ID: 26c2f9d11c8e
Revises: b3f9c07ae214
Create Date: 2026-08-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from lib.db.migration_helpers import preserve_sqlite_indexes

revision: str = "26c2f9d11c8e"
down_revision: str | Sequence[str] | None = "b3f9c07ae214"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "batches",
        sa.Column("batch_id", sa.String(), nullable=False),
        sa.Column("project_name", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("requested_json", sa.Text(), nullable=False),
        sa.Column("blocked_json", sa.Text(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(), server_default="default", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("batch_id"),
    )
    op.create_index(op.f("ix_batches_user_id"), "batches", ["user_id"], unique=False)

    with preserve_sqlite_indexes("tasks"):
        with op.batch_alter_table("tasks", schema=None) as batch_op:
            batch_op.add_column(sa.Column("batch_id", sa.String(), nullable=True))
            batch_op.create_foreign_key("fk_tasks_batch_id_batches", "batches", ["batch_id"], ["batch_id"])
            batch_op.create_index(batch_op.f("ix_tasks_batch_id"), ["batch_id"], unique=False)

    op.create_table(
        "batch_tasks",
        sa.Column("batch_id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("unit_id", sa.String(), nullable=False),
        sa.Column("deduped", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["batches.batch_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.task_id"]),
        sa.PrimaryKeyConstraint("batch_id", "task_id", "unit_id"),
        sa.UniqueConstraint("batch_id", "unit_id", name="uq_batch_tasks_batch_unit"),
    )


def downgrade() -> None:
    op.drop_table("batch_tasks")
    op.drop_index(op.f("ix_tasks_batch_id"), table_name="tasks")
    with preserve_sqlite_indexes("tasks"):
        with op.batch_alter_table("tasks", schema=None) as batch_op:
            batch_op.drop_constraint("fk_tasks_batch_id_batches", type_="foreignkey")
            batch_op.drop_column("batch_id")
    op.drop_index(op.f("ix_batches_user_id"), table_name="batches")
    op.drop_table("batches")
