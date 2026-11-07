"""Expand overrides to model/backend facts; preserve mapped values and clear ComfyUI overrides

Revision ID: a98bcf8749b2
Revises: c5f2a8d61e93
Create Date: 2026-10-15 07:00:00.000000

Overrides expand from video-backend facts to model and backend facts (docs/adr/0104). Existing
``VideoCapabilities`` keys each identify a backend-fact dimension; names stay unchanged, making
the mapping identity and preserving values. New model dimensions (``durations``, ``text_to_image``,
etc.) do not overlap, leaving existing rows unaffected.

ComfyUI capabilities derive only from node bindings (docs/adr/0082), without overrides. Clear existing
overrides on models attached to ComfyUI endpoints or under ComfyUI-protocol providers. Writes pair
these in both directions; recognize both here to cover writes bypassing UI. Execution already ignored
these values, so clearing does not change generation.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a98bcf8749b2"
down_revision: str | Sequence[str] | None = "c5f2a8d61e93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE custom_provider_model
            SET capability_overrides = NULL
            WHERE capability_overrides IS NOT NULL
              AND (
                provider_id IN (SELECT id FROM custom_provider WHERE discovery_format = 'comfyui')
                OR endpoint IN (SELECT 'ce-' || CAST(id AS VARCHAR) FROM custom_endpoint WHERE kind = 'comfyui')
              )
            """
        )
    )


def downgrade() -> None:
    # Cleared overrides were already ineffective at execution; downgrade does not restore them.
    pass
