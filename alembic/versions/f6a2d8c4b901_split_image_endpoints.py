"""Split image endpoints by dimension-request and metering contracts.

Revision ID: f6a2d8c4b901
Revises: e5b1c9a3d7f2

Old image backends used only API model names, not bindings or aliases. Freeze that rule to backfill
existing models; preserve user-selected other endpoints. Image calls finish synchronously, with no
resumable provider task or checkpoint. Downgrade restores old endpoint names without changing entries,
tasks, or user capability overrides.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a2d8c4b901"
down_revision: str | Sequence[str] | None = "e5b1c9a3d7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUCCESSORS = {
    "ark-seedream": ("ark-seedream-3", "ark-seedream-4"),
    "dashscope-image": ("dashscope-qwen-image", "dashscope-wan-image", "dashscope-qwen-image-edit"),
    "agnes-image": ("agnes-image-pixels", "agnes-image-tier"),
}
_PROVIDERS = {
    "ark": "ark-seedream",
    "ark-agent-plan": "ark-seedream",
    "dashscope": "dashscope-image",
    "agnes": "agnes-image",
}


def _endpoint(old: str, model: str) -> str:
    lowered = model.lower()
    if old == "ark-seedream":
        return "ark-seedream-3" if "seedream-3" in lowered else "ark-seedream-4"
    if old == "dashscope-image":
        if lowered.startswith("wan"):
            return "dashscope-wan-image"
        return "dashscope-qwen-image-edit" if "qwen-image-edit" in lowered else "dashscope-qwen-image"
    return "agnes-image-tier" if model.startswith("agnes-image-2.5") else "agnes-image-pixels"


def _tables() -> tuple[sa.TableClause, sa.TableClause]:
    return (
        sa.table(
            "custom_provider_model",
            sa.column("id", sa.Integer),
            sa.column("model_id", sa.String),
            sa.column("endpoint", sa.String),
        ),
        sa.table(
            "builtin_provider_model",
            sa.column("id", sa.Integer),
            sa.column("model_id", sa.String),
            sa.column("endpoint", sa.String),
            sa.column("provider_id", sa.String),
            sa.column("user_added", sa.Boolean),
            sa.column("media_type", sa.String),
        ),
    )


def upgrade() -> None:
    connection = op.get_bind()
    custom, builtin = _tables()
    for row_id, model, old in connection.execute(
        sa.select(custom.c.id, custom.c.model_id, custom.c.endpoint).where(custom.c.endpoint.in_(_SUCCESSORS))
    ).all():
        connection.execute(sa.update(custom).where(custom.c.id == row_id).values(endpoint=_endpoint(old, model)))
    for row_id, provider, model, old in connection.execute(
        sa.select(builtin.c.id, builtin.c.provider_id, builtin.c.model_id, builtin.c.endpoint).where(
            builtin.c.user_added.is_(True), builtin.c.media_type == "image", builtin.c.provider_id.in_(_PROVIDERS)
        )
    ).all():
        expected = _PROVIDERS[provider]
        if old is None or old == expected:
            connection.execute(
                sa.update(builtin).where(builtin.c.id == row_id).values(endpoint=_endpoint(expected, model))
            )


def downgrade() -> None:
    connection = op.get_bind()
    custom, builtin = _tables()
    for old, successors in _SUCCESSORS.items():
        connection.execute(sa.update(custom).where(custom.c.endpoint.in_(successors)).values(endpoint=old))
        connection.execute(
            sa.update(builtin)
            .where(
                builtin.c.endpoint.in_(successors),
                builtin.c.user_added.is_(True),
                builtin.c.provider_id.in_([p for p, e in _PROVIDERS.items() if e == old]),
            )
            .values(endpoint=old)
        )
