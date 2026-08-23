"""Shared parsing for per-model system settings.

The database stores system settings as strings. ``model_settings`` mirrors the
project.json shape so project and global overrides use the same composite
``provider/model`` key.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

MODEL_SETTINGS_KEY = "model_settings"

type ModelSettings = dict[str, dict[str, str | None]]


def normalize_model_settings(value: object, *, warn: bool = False) -> ModelSettings:
    """Return the supported subset of a decoded ``model_settings`` value."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        if warn:
            logger.warning("system model_settings has non-dict type %s; ignoring it", type(value).__name__)
        return {}

    normalized: ModelSettings = {}
    for raw_key, raw_entry in value.items():
        if not isinstance(raw_key, str) or "/" not in raw_key or not isinstance(raw_entry, dict):
            if warn:
                logger.warning("ignoring malformed system model_settings entry %r", raw_key)
            continue
        raw_resolution = raw_entry.get("resolution")
        if raw_resolution is None:
            normalized[raw_key] = {"resolution": None}
            continue
        if not isinstance(raw_resolution, str):
            if warn:
                logger.warning("ignoring non-string resolution for system model_settings entry %r", raw_key)
            continue
        normalized[raw_key] = {"resolution": raw_resolution.strip() or None}
    return normalized


def parse_model_settings(raw: str | None) -> ModelSettings:
    """Decode a system-setting JSON string defensively."""
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        logger.warning("system model_settings is not valid JSON; ignoring it")
        return {}
    return normalize_model_settings(decoded, warn=True)


def serialize_model_settings(value: object) -> str:
    """Encode normalized model settings in a stable representation."""
    return json.dumps(normalize_model_settings(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def configured_resolution(settings: ModelSettings, provider_id: str, model_id: str) -> str | None:
    """Read a non-empty resolution for one composite model key."""
    return settings.get(f"{provider_id}/{model_id}", {}).get("resolution") or None
