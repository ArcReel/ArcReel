"""v11→v12: add empty derivative tables to existing characters without changing behavior.

``derivatives`` is a character child table (name → relative appearance change + sheet path), only for
``AssetSpec.supports_derivatives`` types. Empty tables let readers ignore absent-versus-empty distinctions.

Change one ``project.json`` field only, without scripts/drafts/manifests. Empty tables change no
persisted artifact identity and introduce no references."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from lib.infra.json_io import atomic_write_json, load_json
from lib.project.asset_types import ASSET_SPECS, DERIVATIVES_FIELD

_TARGET_VERSION = 12


def migrate_project_dict(project: dict[str, Any]) -> dict[str, Any]:
    """Pure, idempotent v11→v12 project-dict conversion.

    Fill missing fields only, preserving existing derivatives and nondict manually edited buckets/entries/
    tables. DataValidator separately reports structural errors; overwriting dirty values would erase user
    content and hide original shapes. Leave schema_version to file-level commit."""
    data = copy.deepcopy(project)
    for spec in ASSET_SPECS.values():
        if not spec.supports_derivatives:
            continue
        bucket = data.get(spec.bucket_key)
        if not isinstance(bucket, dict):
            continue
        for entry in bucket.values():
            if isinstance(entry, dict) and DERIVATIVES_FIELD not in entry:
                entry[DERIVATIVES_FIELD] = {}
    return data


def migrate_v11_to_v12(project_dir: Path) -> None:
    """File-level v11→v12 migration: one atomic write, retryable after crashes with old/new values and no partial state."""
    pj = project_dir / "project.json"
    if not pj.exists():
        return
    data = load_json(pj)
    if not isinstance(data, dict):
        raise ValueError("project.json 必须是对象")
    # Match runner int normalization: legacy project.json may store version strings.
    if int(data.get("schema_version") or 0) >= _TARGET_VERSION:
        return
    migrated = migrate_project_dict(data)
    migrated["schema_version"] = _TARGET_VERSION
    atomic_write_json(pj, migrated)


__all__ = ["migrate_project_dict", "migrate_v11_to_v12"]
