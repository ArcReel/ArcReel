"""add path_overrides to custom provider model

Revision ID: c5f2a8d61e93
Revises: d4e8a1f6c392
Create Date: 2026-10-15 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5f2a8d61e93"
down_revision: str | Sequence[str] | None = "d4e8a1f6c392"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """加模型级路径覆盖列（请求名 → 路径，NULL = 全部按调用端点的默认路径）。"""
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.add_column(sa.Column("path_overrides", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.drop_column("path_overrides")
