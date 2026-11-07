"""v17→v18: remove Agent-turn fields from timeline revisions and backfill gap-skipped artifacts.

Queued turns can receive several user messages, making revision ``agent_turn`` ambiguous and unused
in display. Revision models reject extra fields; remove only this persisted field, preserving all else.
Timelines are outside manifests, so no registrations change. Back up each file before writing, without
business timeline writers or advancing project activity. Runner owns ``project.json`` backups.

From v18, gaps block generation only while basis remains projectable (``docs/adr/0098``). Plan before/
after and backfill previously skipped artifacts without changing existing registrations; see
:mod:`lib.project.project_migrations.input_gap_backfill`. Then fingerprint registered video/narration
only when formal files match selected snapshots; see :mod:`lib.project.project_migrations.content_digest_backfill`.

Also rewrite renamed model references in ``project.json`` to successors using this module's frozen
map. Preserve retired references without successors for runtime user reselection. Artifact basis
excludes model fields, so no registered currency changes."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import MANIFEST_FILENAME
from lib.artifacts.formal_write import project_metadata_lock
from lib.config.model_setting_keys import PROJECT_MODEL_SETTING_KEYS
from lib.infra.json_io import atomic_write_json
from lib.project.project_migration_report import ArtifactBackfillOutcome
from lib.project.project_migrations.backups import ensure_versioned_backup
from lib.project.project_migrations.content_digest_backfill import backfill_content_digests
from lib.project.project_migrations.input_gap_backfill import backfill_input_gap_artifacts
from lib.project.project_schema import parse_project_schema_version

TARGET_SCHEMA_VERSION = 18
_FROM_VERSION = TARGET_SCHEMA_VERSION - 1
_REMOVED_FIELD = "agent_turn"

#: Frozen retirement map for this migration only; never add/remove entries, keeping historical upgrades reproducible.
#: Runtime no longer rewrites hardcoded maps; later retirement lives only in catalog lifecycles (docs/adr/0106).
_RETIRED_MODEL_REFERENCES: dict[str, str] = {
    # Text.
    "gemini-aistudio/gemini-3.1-flash-lite-preview": "gemini-aistudio/gemini-3.1-flash-lite",
    "gemini-vertex/gemini-3.1-flash-lite-preview": "gemini-vertex/gemini-3.1-flash-lite",
    # Ark Seed 2.0 Lite/Mini 260215 and xAI Grok 4.1 Fast retired; use active same-tier models.
    "ark/doubao-seed-2-0-lite-260215": "ark/doubao-seed-2-1-lite-260915",
    "ark/doubao-seed-2-0-mini-260215": "ark/doubao-seed-2-0-mini-260428",
    "grok/grok-4-1-fast-reasoning": "grok/grok-4.3",
    "grok/grok-4-1-fast-non-reasoning": "grok/grok-4.3",
    # Images: Gemini released; AI Studio/Vertex both use names without -preview.
    "gemini-aistudio/gemini-3-pro-image-preview": "gemini-aistudio/gemini-3-pro-image",
    "gemini-aistudio/gemini-3.1-flash-image-preview": "gemini-aistudio/gemini-3.1-flash-image",
    "gemini-vertex/gemini-3-pro-image-preview": "gemini-vertex/gemini-3-pro-image",
    "gemini-vertex/gemini-3.1-flash-image-preview": "gemini-vertex/gemini-3.1-flash-image",
}


def _is_real_dir(path: Path) -> bool:
    return not path.is_symlink() and not path.is_junction() and path.is_dir()


def _episode_dirs(project_dir: Path) -> Iterator[Path]:
    """``edit_timelines/episode_*`` directories; do not follow symlinks/junctions into files outside projects."""

    root = Path(project_dir) / "edit_timelines"
    if not _is_real_dir(root):
        return
    yield from (directory for directory in sorted(root.glob("episode_*")) if _is_real_dir(directory))


def _timeline_names(directory: Path) -> set[str]:
    return {path.name for path in directory.glob("*.json") if not path.is_symlink() and path.is_file()}


def timeline_files(project_dir: Path) -> Iterator[Path]:
    """All project ``edit_timelines/episode_*/*.json`` files, excluding symlinks."""

    for directory in _episode_dirs(project_dir):
        yield from (directory / name for name in sorted(_timeline_names(directory)))


def timeline_backup_sources(project_dir: Path) -> Iterator[Path]:
    """Timeline backup source paths: existing files plus deleted originals with remaining backups."""

    for directory in _episode_dirs(project_dir):
        names = _timeline_names(directory)
        names.update(
            path.name.partition(".bak.v")[0] for path in directory.glob("*.json.bak.v*") if not path.is_symlink()
        )
        yield from (directory / name for name in sorted(names))


def migrate_timeline_dict(document: Mapping[str, Any]) -> dict[str, Any]:
    """Pure, idempotent removal of revision ``agent_turn``; preserve unknown shapes unchanged."""

    migrated = dict(document)
    revisions = migrated.get("revisions")
    if isinstance(revisions, list):
        migrated["revisions"] = [
            {key: value for key, value in revision.items() if key != _REMOVED_FIELD}
            if isinstance(revision, dict)
            else revision
            for revision in revisions
        ]
    return migrated


def migrate_project_model_references(project: Mapping[str, Any]) -> dict[str, Any]:
    """Pure, idempotent rewrite of exact retired ``project.json`` model references to successors; preserve nonstrings.

    ``model_settings`` uses ``provider/model`` keys (e.g. image resolution). Move retired settings to successor
    keys or renamed models silently lose resolution. Existing successor settings win, leaving old keys unchanged."""

    migrated = dict(project)
    for key in PROJECT_MODEL_SETTING_KEYS:
        value = migrated.get(key)
        if isinstance(value, str):
            migrated[key] = _RETIRED_MODEL_REFERENCES.get(value, value)
    model_settings = migrated.get("model_settings")
    if isinstance(model_settings, dict):
        renamed: dict[str, Any] = {}
        for key, entry in model_settings.items():
            successor = _RETIRED_MODEL_REFERENCES.get(key, key) if isinstance(key, str) else key
            if successor != key and successor not in model_settings:
                renamed[successor] = entry
            else:
                renamed[key] = entry
        migrated["model_settings"] = renamed
    return migrated


def migrate_v17_to_v18(project_dir: Path) -> ArtifactBackfillOutcome | None:
    """File-level v17→v18 migration: atomic per-file writes, advance schema last; retry after interruptions."""

    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return None
    with project_metadata_lock(project_dir):
        project = json.loads(project_file.read_bytes())
        if not isinstance(project, dict):
            raise ValueError("project.json 必须是对象")
        if parse_project_schema_version(project) >= TARGET_SCHEMA_VERSION:
            return None
        # Back up manifests before rewrites (gap backfill first backs up history), deduplicating both backfills' internal backups.
        ensure_versioned_backup(project_dir / MANIFEST_FILENAME, _FROM_VERSION)
        outcome = backfill_input_gap_artifacts(project_dir, project)
        backfill_content_digests(project_dir)
        for path in timeline_files(project_dir):
            try:
                document = json.loads(path.read_bytes())
            except ValueError:
                # Leave already-unreadable files unchanged for normal unparseable read handling.
                continue
            if not isinstance(document, dict):
                continue
            migrated = migrate_timeline_dict(document)
            if migrated != document:
                ensure_versioned_backup(path, _FROM_VERSION)
                atomic_write_json(path, migrated)
        atomic_write_json(
            project_file, {**migrate_project_model_references(project), "schema_version": TARGET_SCHEMA_VERSION}
        )
    return outcome


__all__ = [
    "TARGET_SCHEMA_VERSION",
    "migrate_project_model_references",
    "migrate_timeline_dict",
    "migrate_v17_to_v18",
    "timeline_backup_sources",
    "timeline_files",
]
