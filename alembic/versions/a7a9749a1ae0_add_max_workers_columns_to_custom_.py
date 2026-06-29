"""add max_workers columns to custom_provider

Revision ID: a7a9749a1ae0
Revises: 7fb52d06b50e
Create Date: 2026-06-29 10:56:55.664949

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7a9749a1ae0"
down_revision: str | Sequence[str] | None = "7fb52d06b50e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    Additive nullable per-lane concurrency columns. No backfill — existing rows
    are correct with NULL (NULL = unset → capacity loader falls back to global
    default), and none of the columns participates in any WHERE/filter.
    """
    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.add_column(sa.Column("image_max_workers", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("video_max_workers", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("audio_max_workers", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.drop_column("audio_max_workers")
        batch_op.drop_column("video_max_workers")
        batch_op.drop_column("image_max_workers")
