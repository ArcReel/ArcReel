"""Record endpoints for user-added built-in models, backfilling the backend actually used before upgrade

Revision ID: c4e8a1f7b3d2
Revises: b7f2a6d91c40
Create Date: 2026-11-04 12:00:00.000000

Built-in provider models first select a built-in endpoint, which constructs the backend. New
``builtin_provider_model`` ``endpoint`` applies only to user-added models; official-model enablement
rows stay unset and follow official endpoints.

Backfill reproduces actual pre-upgrade assembly inputs: use the sole backend when a built-in provider
has only one for a media type. MiniMax video chooses declarative endpoints by API model name (model key),
not bindings, matched-entry official names, or aliases. Both rules match pre-upgrade
``lib.backends.backend_assembly.specs``; freeze literals here without application imports. Migrations
are point-in-time snapshots; later routing changes must not rewrite migrated rows. Media types absent
from the table (unsupported by that provider before upgrade) remain unset.

Downgrade removes the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4e8a1f7b3d2"
down_revision: str | Sequence[str] | None = "b7f2a6d91c40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Sole pre-upgrade backend per built-in provider + media type (except MiniMax video; see _minimax_video_endpoint).
_CHANNELS: dict[tuple[str, str], str] = {
    ("agnes", "image"): "agnes-image",
    ("agnes", "text"): "agnes-chat",
    ("agnes", "video"): "agnes-video",
    ("ark", "image"): "ark-seedream",
    ("ark", "text"): "ark-chat",
    ("ark", "video"): "ark-seedance",
    ("ark-agent-plan", "image"): "ark-seedream",
    ("ark-agent-plan", "text"): "ark-chat",
    ("ark-agent-plan", "video"): "ark-seedance",
    ("dashscope", "audio"): "dashscope-tts",
    ("dashscope", "image"): "dashscope-image",
    ("dashscope", "text"): "openai-chat",
    ("dashscope", "video"): "dashscope-async-video",
    ("gemini-aistudio", "audio"): "gemini-tts",
    ("gemini-aistudio", "image"): "gemini-image",
    ("gemini-aistudio", "text"): "gemini-generate",
    ("gemini-aistudio", "video"): "gemini-video",
    ("gemini-vertex", "audio"): "gemini-tts",
    ("gemini-vertex", "image"): "gemini-image",
    ("gemini-vertex", "text"): "gemini-generate",
    ("gemini-vertex", "video"): "gemini-video",
    ("grok", "image"): "grok-image",
    ("grok", "text"): "grok-chat",
    ("grok", "video"): "grok-video",
    ("kling", "image"): "kling-image",
    ("kling", "video"): "kling-video",
    ("minimax", "audio"): "minimax-tts",
    ("minimax", "image"): "minimax-image",
    ("minimax", "text"): "openai-chat",
    ("openai", "image"): "openai-images",
    ("openai", "text"): "openai-responses",
    ("openai", "video"): "openai-video",
    ("vidu", "image"): "vidu-image",
    ("vidu", "video"): "vidu-video",
}


def _minimax_video_endpoint(model_id: str) -> str:
    """Pre-upgrade built-in MiniMax API-model-name endpoint rules, copied verbatim."""
    model = model_id.lower()
    if model_id == "S2V-01":
        return "minimax-s2v-01"
    if "minimax-h3" in model:
        return "minimax-h3"
    if "hailuo" in model and "fast" in model:
        return "minimax-hailuo-v1-fast"
    return "minimax-hailuo-v1"


def _endpoint_for(provider_id: str, model_id: str, media_type: str | None) -> str | None:
    if media_type is None:
        return None
    if (provider_id, media_type) == ("minimax", "video"):
        return _minimax_video_endpoint(model_id)
    return _CHANNELS.get((provider_id, media_type))


def upgrade() -> None:
    with op.batch_alter_table("builtin_provider_model") as batch:
        batch.add_column(sa.Column("endpoint", sa.String(length=64), nullable=True))

    connection = op.get_bind()
    table = sa.table(
        "builtin_provider_model",
        sa.column("id", sa.Integer),
        sa.column("provider_id", sa.String),
        sa.column("model_id", sa.String),
        sa.column("user_added", sa.Boolean),
        sa.column("media_type", sa.String),
        sa.column("endpoint", sa.String),
    )
    rows = connection.execute(
        sa.select(table.c.id, table.c.provider_id, table.c.model_id, table.c.media_type).where(table.c.user_added)
    ).all()
    for row_id, provider_id, model_id, media_type in rows:
        endpoint = _endpoint_for(provider_id, model_id, media_type)
        if endpoint is not None:
            connection.execute(sa.update(table).where(table.c.id == row_id).values(endpoint=endpoint))


def downgrade() -> None:
    with op.batch_alter_table("builtin_provider_model") as batch:
        batch.drop_column("endpoint")
