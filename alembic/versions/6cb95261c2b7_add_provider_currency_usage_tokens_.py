"""add provider currency usage_tokens rename cost_usd to cost_amount

Revision ID: 6cb95261c2b7
Revises: ecbb53758daa
Create Date: 2026-03-17 14:37:08.585442

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6cb95261c2b7'
down_revision: Union[str, Sequence[str], None] = 'ecbb53758daa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite requires batch mode for column rename
    with op.batch_alter_table('api_calls') as batch_op:
        # Rename cost_usd → cost_amount
        batch_op.alter_column('cost_usd', new_column_name='cost_amount')
        # Add new columns
        batch_op.add_column(sa.Column('currency', sa.String(), server_default='USD', nullable=False))
        batch_op.add_column(sa.Column('provider', sa.String(), server_default='gemini', nullable=False))
        batch_op.add_column(sa.Column('usage_tokens', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('api_calls') as batch_op:
        batch_op.drop_column('usage_tokens')
        batch_op.drop_column('provider')
        batch_op.drop_column('currency')
        batch_op.alter_column('cost_amount', new_column_name='cost_usd')
