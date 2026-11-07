"""Remove api_calls.generate_audio server default; leave non-video rows unset

Revision ID: b66f6d617e3f
Revises: 23c60be146cc
Create Date: 2026-09-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b66f6d617e3f"
down_revision: str | Sequence[str] | None = "23c60be146cc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # generate_audio matters only for video calls; server default true incorrectly labels undeclared non-video calls
    # as audible. Removing the default leaves undeclared values NULL and clears historically defaulted non-video rows.
    with op.batch_alter_table("api_calls") as batch_op:
        batch_op.alter_column("generate_audio", server_default=None)
    op.execute(sa.text("UPDATE api_calls SET generate_audio = NULL WHERE call_type != 'video'"))


def downgrade() -> None:
    with op.batch_alter_table("api_calls") as batch_op:
        batch_op.alter_column("generate_audio", server_default=sa.true())
