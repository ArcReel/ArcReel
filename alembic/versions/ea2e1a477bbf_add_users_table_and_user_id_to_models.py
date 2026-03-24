"""add users table and user_id to models

Revision ID: ea2e1a477bbf
Revises: 802fa55d8aff
Create Date: 2026-03-24 17:03:19.868079

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ea2e1a477bbf'
down_revision: Union[str, Sequence[str], None] = '802fa55d8aff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Create users table first (FK dependency)
    op.create_table(
        'users',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('role', sa.String(), server_default='user', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username'),
    )

    # 2. Insert default user
    op.execute("""
        INSERT INTO users (id, username, role, is_active, created_at, updated_at)
        VALUES ('default', 'admin', 'admin', 1,
                strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'),
                strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))
    """)

    # 3. Add user_id to tasks
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('user_id', sa.String(), server_default='default', nullable=False)
        )
        batch_op.create_index(batch_op.f('ix_tasks_user_id'), ['user_id'], unique=False)

    # 4. Fix api_calls: fill NULL created_at, add updated_at and user_id
    op.execute("UPDATE api_calls SET created_at = started_at WHERE created_at IS NULL")

    with op.batch_alter_table('api_calls', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'updated_at', sa.DateTime(timezone=True),
                server_default=sa.text("(strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column('user_id', sa.String(), server_default='default', nullable=False)
        )
        batch_op.alter_column(
            'created_at', existing_type=sa.DATETIME(), nullable=False
        )
        batch_op.create_index(batch_op.f('ix_api_calls_user_id'), ['user_id'], unique=False)

    # 5. Add updated_at and user_id to api_keys
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'updated_at', sa.DateTime(timezone=True),
                server_default=sa.text("(strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column('user_id', sa.String(), server_default='default', nullable=False)
        )
        batch_op.create_index(batch_op.f('ix_api_keys_user_id'), ['user_id'], unique=False)

    # 6. Add user_id to agent_sessions
    with op.batch_alter_table('agent_sessions', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('user_id', sa.String(), server_default='default', nullable=False)
        )
        batch_op.create_index(
            batch_op.f('ix_agent_sessions_user_id'), ['user_id'], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('agent_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_agent_sessions_user_id'))
        batch_op.drop_column('user_id')

    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_api_keys_user_id'))
        batch_op.drop_column('user_id')
        batch_op.drop_column('updated_at')

    with op.batch_alter_table('api_calls', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_api_calls_user_id'))
        batch_op.alter_column(
            'created_at', existing_type=sa.DATETIME(), nullable=True
        )
        batch_op.drop_column('user_id')
        batch_op.drop_column('updated_at')

    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tasks_user_id'))
        batch_op.drop_column('user_id')

    op.drop_table('users')
