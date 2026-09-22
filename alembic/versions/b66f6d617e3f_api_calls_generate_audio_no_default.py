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
    # generate_audio 只对视频调用有意义；服务端默认 true 会让调用方没有声明的非视频行也被填成
    # 「有声」。去掉默认后未声明即 NULL，并把历史上被默认填过的非视频行清空。
    with op.batch_alter_table("api_calls") as batch_op:
        batch_op.alter_column("generate_audio", server_default=None)
    op.execute(sa.text("UPDATE api_calls SET generate_audio = NULL WHERE call_type != 'video'"))


def downgrade() -> None:
    with op.batch_alter_table("api_calls") as batch_op:
        batch_op.alter_column("generate_audio", server_default=sa.true())
