"""Allow task-specific voice sample IDs in usage records.

Revision ID: b793a01c4e62
Revises: f6d2a8c4b931
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b793a01c4e62"
down_revision: str | Sequence[str] | None = "f6d2a8c4b931"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("api_calls") as batch:
        batch.alter_column("segment_id", existing_type=sa.String(20), type_=sa.String(), existing_nullable=True)


def downgrade() -> None:
    # Refuse to truncate usage history when reverting after long IDs have been recorded.
    if op.get_bind().execute(sa.text("SELECT 1 FROM api_calls WHERE length(segment_id) > 20 LIMIT 1")).first():
        raise RuntimeError("Cannot narrow api_calls.segment_id: usage records contain IDs longer than 20 characters")
    with op.batch_alter_table("api_calls") as batch:
        batch.alter_column("segment_id", existing_type=sa.String(), type_=sa.String(20), existing_nullable=True)
