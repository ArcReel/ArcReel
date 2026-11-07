"""Built-in provider models: official-model enablement and user-added models

Revision ID: e3a7c91f5d24
Revises: b25cfcfb8bad
Create Date: 2026-10-16 10:00:00.000000

Built-in models can be enabled/disabled individually; users can add models by ID (docs/adr/0099, 0106).
Without a row, an official model is enabled unless delisted, preserving previously visible models.
Insert disabled rows for the 3 previously hidden legacy Agnes models; they remain disabled.
No user-added models exist in historical data.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "e3a7c91f5d24"
down_revision: str | Sequence[str] | None = "b25cfcfb8bad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Models formerly ``hidden=True`` in the registry: (provider, model key).
_HIDDEN_MODELS: tuple[tuple[str, str], ...] = (
    ("agnes", "agnes-2.0-flash"),
    ("agnes", "agnes-image-2.1-flash"),
    ("agnes", "agnes-video-v2.0"),
)


def upgrade() -> None:
    table = op.create_table(
        "builtin_provider_model",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("user_added", sa.Boolean(), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=True),
        sa.Column("multiplier", sa.Float(), nullable=False, server_default=sa.text("1")),
        sa.Column("entry_binding", sa.String(length=255), nullable=True),
        sa.Column("capability_overrides", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "model_id", name="uq_builtin_provider_model"),
    )
    op.create_index("ix_builtin_provider_model_provider_id", "builtin_provider_model", ["provider_id"])
    now = datetime.now(UTC)
    op.bulk_insert(
        table,
        [
            {
                "provider_id": provider_id,
                "model_id": model_id,
                "enabled": False,
                "user_added": False,
                "multiplier": 1.0,
                "created_at": now,
                "updated_at": now,
            }
            for provider_id, model_id in _HIDDEN_MODELS
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_builtin_provider_model_provider_id", table_name="builtin_provider_model")
    op.drop_table("builtin_provider_model")
