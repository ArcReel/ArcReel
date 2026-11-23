"""api_calls.generate_audio 去掉服务端默认，非视频行留空

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
    with op.batch_alter_table("api_calls") as batch_op:
        batch_op.alter_column("generate_audio", server_default=None)
    op.execute(sa.text("UPDATE api_calls SET generate_audio = NULL WHERE call_type != 'video'"))


def downgrade() -> None:
    with op.batch_alter_table("api_calls") as batch_op:
        batch_op.alter_column("generate_audio", server_default=sa.true())
