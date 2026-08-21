"""add durable background jobs

Revision ID: ca8d7e31b6f2
Revises: 1240a4fcfcbc
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ca8d7e31b6f2"
down_revision: str | Sequence[str] | None = "1240a4fcfcbc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=False),
        sa.Column("progress_current", sa.Integer(), nullable=False),
        sa.Column("progress_total", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_detail", sa.String(length=200), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(
        "idx_background_jobs_one_active_per_type",
        "background_jobs",
        ["job_type"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued', 'running')"),
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "idx_background_jobs_status_queued",
        "background_jobs",
        ["status", "queued_at"],
        unique=False,
    )
    op.create_index(
        "idx_background_jobs_type_updated",
        "background_jobs",
        ["job_type", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_background_jobs_type_updated", table_name="background_jobs")
    op.drop_index("idx_background_jobs_status_queued", table_name="background_jobs")
    op.drop_index("idx_background_jobs_one_active_per_type", table_name="background_jobs")
    op.drop_table("background_jobs")
