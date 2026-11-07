# Permanent schema_version=1 migration, retained over time like v0→v1.
#
# Normalize legacy project.json providers (gemini/aistudio/vertex/seedance) into canonical IDs,
# splitting legacy image_backend into image_provider_t2i/i2i. Later resolution assumes canonical IDs,
# without read-time normalization.
#
# Retain chain continuity: missing steps cause `no migrator from v1`, and v0 upgrades also require
# v1→v2. Existing v0/v1 projects, old archives, and restored backups depend on it.
"""v1→v2: normalize legacy provider names and split legacy image_backend."""

from __future__ import annotations

from pathlib import Path

from lib.config.registry import PROVIDER_REGISTRY
from lib.infra.json_io import atomic_write_json, load_json

# The sole legacy alias map, unioning three former tables without inventing aliases.
# Every target must be a real canonical PROVIDER_REGISTRY ID.
_LEGACY_PROVIDER_ALIASES: dict[str, str] = {
    "gemini": "gemini-aistudio",
    "aistudio": "gemini-aistudio",
    "vertex": "gemini-vertex",
    "seedance": "ark",
}

assert all(target in PROVIDER_REGISTRY for target in _LEGACY_PROVIDER_ALIASES.values()), (
    "legacy 别名目标必须是规范 provider id"
)

# Normalize seven project.json provider-ID fields individually.
_PROVIDER_FIELDS: tuple[str, ...] = (
    "video_backend",
    "image_backend",
    "image_provider_t2i",
    "image_provider_i2i",
    "text_backend_script",
    "text_backend_overview",
    "text_backend_style",
)


def _normalize_field(value: str) -> str:
    """Normalize provider portions of ``"provider/model"`` or bare ``"provider"`` fields.

    Strip before alias matching; whitespace-padded legacy names such as ``" gemini / model "`` otherwise
    miss matches and remain unnormalized, defeating the one-time clean break."""
    if "/" in value:
        provider, model = value.split("/", 1)
        provider, model = provider.strip(), model.strip()
        canonical = _LEGACY_PROVIDER_ALIASES.get(provider, provider)
        # Missing models (e.g. "gemini /") return canonical providers only, avoiding noncanonical trailing slashes.
        return f"{canonical}/{model}" if model else canonical
    stripped = value.strip()
    return _LEGACY_PROVIDER_ALIASES.get(stripped, stripped)


def migrate_project_dict(project: dict) -> dict:
    """Pure, idempotent v1→v2 project-dict conversion.

    First split legacy image_backend into missing t2i/i2i fields by copying originals; then normalize
    provider portions of seven fields; finally remove image_backend.
    Leave schema_version to file-level commit."""
    data = dict(project)

    # 1. Split image_backend into image_provider_t2i / _i2i, filling missing slots only.
    legacy = data.get("image_backend")
    if isinstance(legacy, str) and legacy:
        data.setdefault("image_provider_t2i", legacy)
        data.setdefault("image_provider_i2i", legacy)

    # 2. Normalize provider fields.
    for key in _PROVIDER_FIELDS:
        value = data.get(key)
        if isinstance(value, str) and value:
            data[key] = _normalize_field(value)

    # 3. Remove the legacy single field.
    data.pop("image_backend", None)

    return data


def migrate_v1_to_v2(project_dir: Path) -> None:
    """File-level v1→v2 migration: one atomic write, inherently retryable after crashes with old/new values and no partial state."""
    pj = project_dir / "project.json"
    if not pj.exists():
        return
    data = load_json(pj)
    # or 0: explicit null equals absent (v0); None >= 2 would raise TypeError.
    if (data.get("schema_version") or 0) >= 2:
        return
    migrated = migrate_project_dict(data)
    migrated["schema_version"] = 2
    atomic_write_json(pj, migrated)
