"""Move custom models to catalog pricing; retain only durations/token limits differing from entries and presets

Revision ID: c7e2a9d4b6f1
Revises: a98bcf8749b2
Create Date: 2026-10-15 12:00:00.000000

Custom models use matched-entry price lists; multipliers express channel differences (docs/adr/0100, 0106):

- Delete ``price_input``, ``price_output``, ``currency``, ``price_unit``: discard old prices without inferring multipliers.
- Add ``multiplier`` (provider-model multiplier, default 1) and ``entry_binding`` (default absent).
- Remove ``supported_durations`` and ``max_output_tokens``. Match shipped entries with docs/adr/0105
  name rules only for comparison, not storage. Convert values to overrides only when differing from
  both entry values and name-inferred duration presets, also when no entry matches. Output tokens
  have no preset: any value without a matched entry becomes an override. Existing same-name overrides win unchanged.
- Compare ComfyUI durations only with native durations derived from endpoint node bindings; convert
  differences to ``durations`` overrides (docs/adr/0082, 0104); discard if the endpoint cannot drive duration.
- Preserve default resolution, endpoint, and other overrides.

Compare against shipped snapshots, name rules, and endpoint registry, importing application code because
these are exactly upgraded runtime fallback values. Existing durations also contain old migration
``4c643f3ff5b9`` presets; copy that historical table verbatim below and also treat those as presets.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "c7e2a9d4b6f1"
down_revision: str | Sequence[str] | None = "a98bcf8749b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

# Preset table copied verbatim from migration 4c643f3ff5b9 when it backfilled existing rows.
_BACKFILL_PRESETS: list[tuple[re.Pattern[str], list[int]]] = [
    (re.compile(r"^sora-2(-pro)?(-\d{4}-\d{2}-\d{2})?$", re.I), [4, 8, 12]),
    (re.compile(r"sora.*pro", re.I), [6, 10, 12, 16, 20]),
    (re.compile(r"veo-?\d", re.I), [4, 6, 8]),
    (re.compile(r"kling[-.]?(o1|v?[123](\.\d+)?)", re.I), [5, 10]),
    (re.compile(r"^(runway[-.]?)?gen-?\d", re.I), [5, 8, 10]),
    (re.compile(r"\bray-?\d", re.I), [5, 10]),
    (re.compile(r"dreamina|seedance", re.I), list(range(4, 16))),
    (re.compile(r"jimeng", re.I), list(range(4, 16))),
    (re.compile(r"happyhorse", re.I), list(range(3, 16))),
    (re.compile(r"grok[-.]?imagine", re.I), list(range(1, 16))),
    (re.compile(r"vidu", re.I), list(range(1, 17))),
    (re.compile(r"pixverse|^v[56](\.\d+)?$", re.I), list(range(1, 16))),
    (re.compile(r"hailuo|minimax", re.I), [6]),
    (re.compile(r"wan-?\d", re.I), [4, 5]),
    (re.compile(r"pika", re.I), [3, 5, 10]),
]

_PRICE_COLUMNS = ("price_input", "price_output", "currency", "price_unit")

_models = sa.table(
    "custom_provider_model",
    sa.column("id", sa.Integer),
    sa.column("model_id", sa.String),
    sa.column("endpoint", sa.String),
    sa.column("supported_durations", sa.Text),
    sa.column("max_output_tokens", sa.Integer),
    sa.column("capability_overrides", sa.JSON),
)
_endpoints = sa.table(
    "custom_endpoint",
    sa.column("id", sa.Integer),
    sa.column("kind", sa.String),
    sa.column("media_type", sa.String),
    sa.column("definition", sa.JSON),
)


def _backfill_preset(model_id: str) -> list[int]:
    for pattern, durations in _BACKFILL_PRESETS:
        if pattern.search(model_id):
            return list(durations)
    return [4, 8]


def _as_json(raw: object) -> object:
    """SQLite may return JSON columns as text."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return raw


def _stored_durations(raw: object) -> list[int] | None:
    value = _as_json(raw)
    if not isinstance(value, list) or not value:
        return None
    if any(type(d) is not int or d <= 0 for d in value):
        return None
    return sorted(set(value))


def _endpoint_facts(endpoint: str, custom_endpoints: Mapping[int, tuple[str, str, object]]) -> tuple[str | None, Any]:
    """Endpoint media type and ComfyUI definition (otherwise ``None``); media type is ``None`` if endpoint is gone."""
    from lib.custom_provider import is_custom_endpoint, parse_endpoint_key

    if is_custom_endpoint(endpoint):
        try:
            row = custom_endpoints.get(parse_endpoint_key(endpoint))
        except ValueError:
            return None, None
        if row is None:
            return None, None
        kind, media_type, definition = row
        return media_type, (_as_json(definition) if kind == "comfyui" else None)
    from lib.custom_provider.endpoints import endpoint_to_media_type

    try:
        return endpoint_to_media_type(endpoint), None
    except ValueError:
        return None, None


def _migrated_overrides(
    model_id: str,
    media_type: str | None,
    comfyui_definition: Any,
    durations: list[int] | None,
    max_output_tokens: int | None,
    overrides: dict[str, object],
) -> dict[str, object]:
    """Merge existing durations/token limits into overrides per docs/adr/0106; existing same-name overrides win."""
    from arcreel_catalog_core import TextFacts, VideoFacts
    from lib.catalog.query import find_entry
    from lib.custom_provider.duration_presets import infer_supported_durations

    merged = dict(overrides)
    if media_type == "video" and durations and "durations" not in merged:
        if comfyui_definition is not None:
            from lib.custom_provider.comfyui.capabilities import default_supported_durations

            try:
                native = default_supported_durations(comfyui_definition)
            except (KeyError, TypeError, ValueError):
                native = []
            if native and durations != sorted(set(native)):
                merged["durations"] = durations
        else:
            entry = find_entry(model_id, media_type="video")
            comparable = [infer_supported_durations(model_id), _backfill_preset(model_id)]
            if entry is not None and isinstance(entry.facts, VideoFacts):
                comparable.append(list(entry.facts.durations))
            if all(durations != sorted(set(values)) for values in comparable):
                merged["durations"] = durations
    if (
        media_type == "text"
        and type(max_output_tokens) is int
        and max_output_tokens > 0
        and "max_output_tokens" not in merged
    ):
        entry = find_entry(model_id, media_type="text")
        entry_value = (
            entry.facts.max_output_tokens if entry is not None and isinstance(entry.facts, TextFacts) else None
        )
        if max_output_tokens != entry_value:
            merged["max_output_tokens"] = max_output_tokens
    return merged


def upgrade() -> None:
    bind = op.get_bind()
    custom_endpoints = {
        row.id: (row.kind, row.media_type, row.definition)
        for row in bind.execute(
            sa.select(_endpoints.c.id, _endpoints.c.kind, _endpoints.c.media_type, _endpoints.c.definition)
        )
    }
    rows = bind.execute(
        sa.select(
            _models.c.id,
            _models.c.model_id,
            _models.c.endpoint,
            _models.c.supported_durations,
            _models.c.max_output_tokens,
            _models.c.capability_overrides,
        )
    ).fetchall()
    converted = 0
    for row in rows:
        raw_overrides = _as_json(row.capability_overrides)
        overrides: dict[str, object] = dict(raw_overrides) if isinstance(raw_overrides, dict) else {}
        media_type, comfyui_definition = _endpoint_facts(row.endpoint or "", custom_endpoints)
        merged = _migrated_overrides(
            row.model_id or "",
            media_type,
            comfyui_definition,
            _stored_durations(row.supported_durations),
            row.max_output_tokens,
            overrides,
        )
        if merged != overrides:
            converted += 1
            bind.execute(sa.update(_models).where(_models.c.id == row.id).values(capability_overrides=merged or None))
    if converted:
        logger.info("custom_provider_model: %d 个模型的可选时长或最大输出 token 转为能力覆盖", converted)

    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        for column in (*_PRICE_COLUMNS, "supported_durations", "max_output_tokens"):
            batch_op.drop_column(column)
        batch_op.add_column(sa.Column("multiplier", sa.Float(), nullable=False, server_default=sa.text("1")))
        batch_op.add_column(sa.Column("entry_binding", sa.String(length=255), nullable=True))


def downgrade() -> None:
    # Old prices were discarded and remain empty after downgrade; overrides stay, with old readers also preferring them.
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.drop_column("entry_binding")
        batch_op.drop_column("multiplier")
        batch_op.add_column(sa.Column("price_unit", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("price_input", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("price_output", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("currency", sa.String(length=8), nullable=True))
        batch_op.add_column(sa.Column("supported_durations", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("max_output_tokens", sa.Integer(), nullable=True))
