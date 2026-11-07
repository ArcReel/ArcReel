"""Split Ark Seedance endpoints into three generations; backfill the actual pre-upgrade generation

Revision ID: e5b1c9a3d7f2
Revises: c4e8a1f7b3d2
Create Date: 2026-11-04 18:00:00.000000

``ark-seedance`` becomes ``ark-seedance-1``, ``ark-seedance-2``, ``ark-seedance-2-5``. All share task
creation/polling, but differ in request bodies (``service_tier``, first-frame ratios), metering
(video-token pixel tables, service tiers), and asset-reference contracts. Previously names selected
generations; now the model’s endpoint determines them.

Reproduce actual pre-upgrade inputs: prefer a valid bound entry, otherwise match by official name,
aliases, and normalized names; determine generation from entry official names/aliases and API model
name. Freeze shipped entry names, merge synced data by version, and overlay entire user copies.
Do not read future application snapshots or name rules.

Rewrite custom-provider models on ``ark-seedance`` and user-added built-in Ark / Ark Agent Plan models
recording ``ark-seedance``. Preserve previously submitted task checkpoints frozen to ``ark-seedance``;
resume maps them to equivalent successors (``lib.backends.resume_guard.RETIRED_ENDPOINT_SUCCESSORS``).
Freeze name-rule literals without application imports: this is a point-in-time snapshot; later routing
changes must not rewrite migrated rows.

Downgrade returns all three to ``ark-seedance``. New endpoint names in checkpoints submitted during
the upgrade are unknown after downgrade, so resume is rejected.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision: str = "e5b1c9a3d7f2"
down_revision: str | Sequence[str] | None = "c4e8a1f7b3d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_ENDPOINT = "ark-seedance"
_NEW_ENDPOINTS = ("ark-seedance-1", "ark-seedance-2", "ark-seedance-2-5")
_ARK_PROVIDERS = ("ark", "ark-agent-plan")


_SNAPSHOT_VERSION = "2026-11-03T12:42:27Z"

_ENTRY_NAMES = {
    "agnes/agnes-video-2.5": ("agnes-video-2.5",),
    "agnes/agnes-video-2.5-flash": ("agnes-video-2.5-flash",),
    "agnes/agnes-video-v2.0": ("agnes-video-v2.0",),
    "alibaba/happyhorse-1.0-i2v": ("happyhorse-1.0-i2v",),
    "alibaba/happyhorse-1.0-r2v": ("happyhorse-1.0-r2v",),
    "alibaba/happyhorse-1.0-t2v": ("happyhorse-1.0-t2v",),
    "alibaba/happyhorse-1.1-i2v": ("happyhorse-1.1-i2v",),
    "alibaba/happyhorse-1.1-r2v": ("happyhorse-1.1-r2v",),
    "alibaba/happyhorse-1.1-t2v": ("happyhorse-1.1-t2v",),
    "alibaba/wan2.7-i2v": ("wan2.7-i2v",),
    "alibaba/wan2.7-r2v": ("wan2.7-r2v",),
    "alibaba/wan2.7-t2v": ("wan2.7-t2v",),
    "alibaba/wan3.0-video": ("wan3.0-video",),
    "bytedance/doubao-seedance-1.5-pro": ("doubao-seedance-1.5-pro", "doubao-seedance-1-5-pro-251215"),
    "bytedance/doubao-seedance-2.0": ("doubao-seedance-2.0", "doubao-seedance-2-0-260128"),
    "bytedance/doubao-seedance-2.0-fast": ("doubao-seedance-2.0-fast", "doubao-seedance-2-0-fast-260128"),
    "bytedance/doubao-seedance-2.0-mini": ("doubao-seedance-2.0-mini", "doubao-seedance-2-0-mini-260615"),
    "bytedance/doubao-seedance-2.5": ("doubao-seedance-2.5", "doubao-seedance-2-5-260628"),
    "google/veo-3.1-fast-generate-001": ("veo-3.1-fast-generate-001",),
    "google/veo-3.1-fast-generate-preview": ("veo-3.1-fast-generate-preview",),
    "google/veo-3.1-generate-001": ("veo-3.1-generate-001",),
    "google/veo-3.1-generate-preview": ("veo-3.1-generate-preview",),
    "google/veo-3.1-lite-generate-preview": ("veo-3.1-lite-generate-preview",),
    "kuaishou/kling-v2-5-turbo": ("kling-v2-5-turbo",),
    "kuaishou/kling-v2-6": ("kling-v2-6",),
    "kuaishou/kling-v3": ("kling-v3",),
    "kuaishou/kling-v3-omni": ("kling-v3-omni",),
    "kuaishou/kling-video-o1": ("kling-video-o1",),
    "minimax/MiniMax-H3": ("MiniMax-H3",),
    "minimax/MiniMax-Hailuo-2.3": ("MiniMax-Hailuo-2.3",),
    "minimax/MiniMax-Hailuo-2.3-Fast": ("MiniMax-Hailuo-2.3-Fast",),
    "minimax/S2V-01": ("S2V-01",),
    "openai/sora-2": ("sora-2",),
    "openai/sora-2-pro": ("sora-2-pro",),
    "shengshu/vidu2.0": ("vidu2.0",),
    "shengshu/viduq3": ("viduq3",),
    "shengshu/viduq3-pro": ("viduq3-pro",),
    "shengshu/viduq3-turbo": ("viduq3-turbo",),
    "xai/grok-imagine-video": ("grok-imagine-video",),
    "xai/grok-imagine-video-1.5": ("grok-imagine-video-1.5",),
    "xai/grok-imagine-video-1.5-lite": ("grok-imagine-video-1.5-lite",),
}


def _normalized(name: str) -> str:
    return name.rpartition("/")[2].lower().replace(".", "-")


def _names(model: str, binding: str | None, entries: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    if binding and binding in entries:
        return entries[binding]
    normalized = _normalized(model)
    for rank in range(4):
        for names in entries.values():
            if (
                (rank == 0 and model == names[0])
                or (rank == 1 and model in names[1:])
                or (rank == 2 and normalized == _normalized(names[0]))
                or (rank == 3 and any(normalized == _normalized(n) for n in names[1:]))
            ):
                return names
    return (model,)


def _generation_endpoint(name: str) -> str | None:
    """Pre-upgrade Seedance generation name rules (``_is_seedance_2_5`` / ``_is_seedance_2``, copied verbatim)."""
    lowered = name.lower()
    if "seedance-2-5" in lowered or "seedance-2.5" in lowered:
        return "ark-seedance-2-5"
    if "seedance-2-0" in lowered or "seedance-2.0" in lowered:
        return "ark-seedance-2"
    return None


def _endpoint_for(model_id: str, entry_binding: str | None, entries: dict[str, tuple[str, ...]]) -> str:
    names = _names(model_id, entry_binding, entries)
    for name in (*names, model_id):
        endpoint = _generation_endpoint(name)
        if endpoint is not None:
            return endpoint
    return "ark-seedance-1"


def _tables() -> tuple[sa.TableClause, sa.TableClause]:
    custom = sa.table(
        "custom_provider_model",
        sa.column("id", sa.Integer),
        sa.column("model_id", sa.String),
        sa.column("endpoint", sa.String),
        sa.column("entry_binding", sa.String),
    )
    builtin = sa.table(
        "builtin_provider_model",
        sa.column("id", sa.Integer),
        sa.column("provider_id", sa.String),
        sa.column("model_id", sa.String),
        sa.column("endpoint", sa.String),
        sa.column("entry_binding", sa.String),
    )
    return custom, builtin


def upgrade() -> None:
    connection = op.get_bind()
    entries = dict(_ENTRY_NAMES)
    raw_catalog = connection.execute(sa.text("SELECT catalog FROM catalog_official_sync WHERE id = 1")).scalar()
    if raw_catalog is not None:
        synced = json.loads(raw_catalog)
        synced_names = {
            f"{entry['maker']}/{entry['name']}": (entry["name"], *entry.get("aliases", []))
            for entry in synced.get("entries", [])
            if entry.get("media_type") == "video"
        }
        if datetime.fromisoformat(synced["dataset_version"]) > datetime.fromisoformat(_SNAPSHOT_VERSION):
            entries = synced_names | {key: names for key, names in entries.items() if key not in synced_names}
        else:
            entries.update({key: names for key, names in synced_names.items() if key not in entries})
    for maker, name, raw in connection.execute(
        sa.text("SELECT maker, name, entry FROM catalog_user_entry WHERE media_type = 'video'")
    ):
        entry = json.loads(raw) if isinstance(raw, str) else raw
        entries[f"{maker}/{name}"] = (entry["name"], *entry.get("aliases", []))
    custom, builtin = _tables()
    custom_rows = connection.execute(
        sa.select(custom.c.id, custom.c.model_id, custom.c.entry_binding).where(custom.c.endpoint == _OLD_ENDPOINT)
    ).all()
    for row_id, model_id, entry_binding in custom_rows:
        connection.execute(
            sa.update(custom)
            .where(custom.c.id == row_id)
            .values(endpoint=_endpoint_for(model_id, entry_binding, entries))
        )
    builtin_rows = connection.execute(
        sa.select(builtin.c.id, builtin.c.model_id, builtin.c.entry_binding).where(
            builtin.c.endpoint == _OLD_ENDPOINT, builtin.c.provider_id.in_(_ARK_PROVIDERS)
        )
    ).all()
    for row_id, model_id, entry_binding in builtin_rows:
        connection.execute(
            sa.update(builtin)
            .where(builtin.c.id == row_id)
            .values(endpoint=_endpoint_for(model_id, entry_binding, entries))
        )


def downgrade() -> None:
    connection = op.get_bind()
    custom, builtin = _tables()
    connection.execute(sa.update(custom).where(custom.c.endpoint.in_(_NEW_ENDPOINTS)).values(endpoint=_OLD_ENDPOINT))
    connection.execute(
        sa.update(builtin)
        .where(builtin.c.endpoint.in_(_NEW_ENDPOINTS), builtin.c.provider_id.in_(_ARK_PROVIDERS))
        .values(endpoint=_OLD_ENDPOINT)
    )
