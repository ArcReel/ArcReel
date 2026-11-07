"""v10→v11: backfill project character-voice binding without changing existing behavior.

Derive any character's ``reference_audio`` → ``reference_audio``, otherwise ``prompt``. Projects with
reference audio already delivered direct audio; inserting defaults would silently change next-generation
voice rules. Both branches preserve actual pre-migration delivery.

Change one ``project.json`` field only, leaving scripts/drafts/manifests. Binding affects next rendering/
currency decisions, never existing persisted artifact identities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.infra.json_io import atomic_write_json, load_json
from lib.speech.character_voice import DEFAULT_CHARACTER_VOICE_BINDING, PROJECT_FIELD, VALID_CHARACTER_VOICE_BINDINGS

_TARGET_VERSION = 11


def _any_character_has_reference_audio(project: dict[str, Any]) -> bool:
    """Whether any project character has reference audio.

    Plaintext project.json may have manually edited nondict character buckets/entries; unparseable shapes
    count as unset, matching prior readers (unreadable ``reference_audio`` could not attach audio anyway)."""
    characters = project.get("characters")
    if not isinstance(characters, dict):
        return False
    for entry in characters.values():
        if isinstance(entry, dict) and isinstance(entry.get("reference_audio"), str) and entry["reference_audio"]:
            return True
    return False


def migrate_project_dict(project: dict[str, Any]) -> dict[str, Any]:
    """Pure, idempotent v10→v11 project-dict conversion.

    Preserve valid existing values, including manually preset fields. Leave schema_version to file-level commit."""
    data = dict(project)
    existing = data.get(PROJECT_FIELD)
    if isinstance(existing, str) and existing in VALID_CHARACTER_VOICE_BINDINGS:
        return data
    data[PROJECT_FIELD] = (
        "reference_audio" if _any_character_has_reference_audio(data) else DEFAULT_CHARACTER_VOICE_BINDING
    )
    return data


def migrate_v10_to_v11(project_dir: Path) -> None:
    """File-level v10→v11 migration: one atomic write, retryable after crashes with old/new values and no partial state."""
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


__all__ = ["migrate_project_dict", "migrate_v10_to_v11"]
