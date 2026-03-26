"""rename seedance provider to ark

Revision ID: 3c6d6152046e
Revises: ea2e1a477bbf
Create Date: 2026-03-26 12:57:36.230376

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '3c6d6152046e'
down_revision: Union[str, Sequence[str], None] = 'ea2e1a477bbf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("UPDATE provider_config SET provider = 'ark' WHERE provider = 'seedance'")
    op.execute(
        "UPDATE system_setting SET value = REPLACE(value, 'seedance/', 'ark/') "
        "WHERE key IN ('default_video_backend', 'default_image_backend')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE provider_config SET provider = 'seedance' WHERE provider = 'ark'")
    op.execute(
        "UPDATE system_setting SET value = REPLACE(value, 'ark/', 'seedance/') "
        "WHERE key IN ('default_video_backend', 'default_image_backend')"
    )
