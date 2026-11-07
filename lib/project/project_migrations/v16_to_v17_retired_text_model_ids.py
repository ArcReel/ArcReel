"""v16→v17: replace retired project text-backend model IDs with current registry keys.

If default text backend/two tier fields in ``project.json`` exactly reference retired models (a frozen
local mapping), rewrite to current IDs. Preserve other previews/nonmatches/nonstring dirty values.

Change ``project.json`` only, leaving scripts/drafts/manifests untouched. Artifact basis excludes text
backends, so no registered artifact currency changes. Runner owns backups."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import project_metadata_lock
from lib.config.model_setting_keys import TEXT_BACKEND_SETTING_KEYS
from lib.infra.json_io import atomic_write_json
from lib.project.project_schema import parse_project_schema_version

TARGET_SCHEMA_VERSION = 17

#: Frozen retirement map for this migration only; never add/remove entries, keeping historical upgrades reproducible.
#: Runtime no longer rewrites from hardcoded maps; later retirement lives only in catalog lifecycles (docs/adr/0106).
_RETIRED_MODEL_REFERENCES: dict[str, str] = {
    # Text.
    "gemini-aistudio/gemini-3.1-flash-lite-preview": "gemini-aistudio/gemini-3.1-flash-lite",
    "gemini-vertex/gemini-3.1-flash-lite-preview": "gemini-vertex/gemini-3.1-flash-lite",
    # Ark Seed 2.0 Lite/Mini 260215 and xAI Grok 4.1 Fast retired; use active models in the same tier.
    "ark/doubao-seed-2-0-lite-260215": "ark/doubao-seed-2-1-lite-260915",
    "ark/doubao-seed-2-0-mini-260215": "ark/doubao-seed-2-0-mini-260428",
    "grok/grok-4-1-fast-reasoning": "grok/grok-4.3",
    "grok/grok-4-1-fast-non-reasoning": "grok/grok-4.3",
    # Images: Gemini image models released; AI Studio/Vertex both use names without -preview.
    "gemini-aistudio/gemini-3-pro-image-preview": "gemini-aistudio/gemini-3-pro-image",
    "gemini-aistudio/gemini-3.1-flash-image-preview": "gemini-aistudio/gemini-3.1-flash-image",
    "gemini-vertex/gemini-3-pro-image-preview": "gemini-vertex/gemini-3-pro-image",
    "gemini-vertex/gemini-3.1-flash-image-preview": "gemini-vertex/gemini-3.1-flash-image",
}


def migrate_project_dict(project: Mapping[str, Any]) -> dict[str, Any]:
    """Pure, idempotent conversion of exact retired text-backend references to current registry keys.

    Leave schema_version to file-level commit."""

    migrated = dict(project)
    for key in TEXT_BACKEND_SETTING_KEYS:
        value = migrated.get(key)
        if isinstance(value, str):
            migrated[key] = _RETIRED_MODEL_REFERENCES.get(value, value)
    return migrated


def migrate_v16_to_v17(project_dir: Path) -> None:
    """File-level v16→v17 migration: one atomic write, retryable after crashes with old/new values and no partial state."""

    project_file = Path(project_dir) / "project.json"
    if not project_file.is_file():
        return
    with project_metadata_lock(project_dir):
        project = json.loads(project_file.read_bytes())
        if not isinstance(project, dict):
            raise ValueError("project.json 必须是对象")
        if parse_project_schema_version(project) >= TARGET_SCHEMA_VERSION:
            return
        atomic_write_json(project_file, {**migrate_project_dict(project), "schema_version": TARGET_SCHEMA_VERSION})


__all__ = ["TARGET_SCHEMA_VERSION", "migrate_project_dict", "migrate_v16_to_v17"]
