"""v9→v10: persisted script-planning/prompt-authoring names follow terminology changes.

Rename three persisted facts without semantic changes:

- Plan/repair-draft filenames under ``drafts/episode_N/``, including legacy ``.md``, change
  ``step1_*`` / ``step2_*`` into ``script_plan_*`` / ``prompt_authoring_*``.
- Three ``project.json`` episode fields: ``step1_review`` / ``stale_step1_revision`` /
  ``stale_step1_rebuilt_revision``.
- Script ``metadata`` field ``step1_revision``.

Rewrite ``.arcreel_artifacts.json`` together: planning keys encode ``episode-step1``, unparseable after
renaming, while ``artifact_path`` points to renamed drafts. Without both rewrites, subsequent reads
mark the whole manifest corrupt. Preserve ``basis_digest``: frozen basis tokens in
``lib.artifacts.artifact_provenance`` guarantee stability across renames.

Coexisting names are normalized beforehand by :mod:`lib.project.project_migrations.legacy_shapes`,
preferring new names and setting aside old files. This step guards only remaining coexistence.

Preflight read-only and compute every payload before creating backups/writing individual files.
Corruption changes no project bytes; runners record repair-needed verdicts."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import decode_artifact_key_parts, encode_artifact_key_parts
from lib.infra.json_io import atomic_write_json, load_json
from lib.infra.path_safety import safe_join
from lib.project.project_migration_failure import ProjectMigrationError
from lib.project.project_migrations.backups import ensure_versioned_backup

_TARGET_VERSION = 10

#: Old → new draft names, historical v9/v10 facts frozen on both sides; later renames must not
#: rewrite this mapping. Runner backup cleanup enumerates both sides.
DRAFT_FILE_RENAMES: dict[str, str] = {
    "step1_normalized_script.json": "script_plan_normalized_script.json",
    "step1_normalized_script.invalid.json": "script_plan_normalized_script.invalid.json",
    "step1_normalized_script.md": "script_plan_normalized_script.md",
    "step1_segments.json": "script_plan_segments.json",
    "step1_segments.invalid.json": "script_plan_segments.invalid.json",
    "step1_segments.md": "script_plan_segments.md",
    "step1_reference_units.json": "script_plan_reference_units.json",
    "step1_reference_units.invalid.json": "script_plan_reference_units.invalid.json",
    "step1_reference_units.md": "script_plan_reference_units.md",
    "step2_reference_script.invalid.json": "prompt_authoring_reference_script.invalid.json",
}

#: ``project.json`` episode-field renames.
_EPISODE_FIELD_RENAMES: dict[str, str] = {
    "step1_review": "script_plan_review",
    "stale_step1_revision": "stale_script_plan_revision",
    "stale_step1_rebuilt_revision": "stale_script_plan_rebuilt_revision",
}

#: Script ``metadata`` field renames.
_SCRIPT_METADATA_FIELD_RENAMES: dict[str, str] = {"step1_revision": "script_plan_revision"}

#: Encoded artifact-key kind renames (``lib.artifacts.artifact_manifest.ArtifactKind``).
_ARTIFACT_KIND_RENAMES: dict[str, str] = {"episode-step1": "episode-script-plan"}

_MANIFEST_FILENAME = ".arcreel_artifacts.json"


def _rename_fields(payload: dict[str, Any], renames: dict[str, str], location: str) -> dict[str, Any]:
    """Rename fields by mapping; reject occupied new names without silent choices."""

    migrated = copy.deepcopy(payload)
    for old, new in renames.items():
        if old not in migrated:
            continue
        if new in migrated:
            raise ValueError(f"{location} 同时存在 {old} 与 {new}，无法判定保留哪一个")
        migrated[new] = migrated.pop(old)
    return migrated


def _rename_artifact_path(artifact_path: str) -> str:
    """Replace manifest draft paths with new names; return other paths unchanged."""

    head, _, name = artifact_path.rpartition("/")
    new_name = DRAFT_FILE_RENAMES.get(name)
    if new_name is None:
        return artifact_path
    return f"{head}/{new_name}" if head else new_name


def migrate_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    """Pure manifest conversion of planning keys and renamed draft paths."""

    entries = payload.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("产物清单 entries 必须是对象")
    migrated_entries: dict[str, Any] = {}
    for encoded_key, entry in entries.items():
        if not isinstance(encoded_key, str):
            raise ValueError("产物清单 key 必须是字符串")
        parts = decode_artifact_key_parts(encoded_key)
        new_key = encoded_key
        if parts is not None and parts.kind in _ARTIFACT_KIND_RENAMES:
            new_key = encode_artifact_key_parts(_ARTIFACT_KIND_RENAMES[parts.kind], parts.components)
        if new_key in migrated_entries:
            raise ValueError(f"产物清单改名后 key 冲突: {new_key}")
        new_entry = entry
        if isinstance(entry, dict) and isinstance(entry.get("artifact_path"), str):
            new_entry = {**entry, "artifact_path": _rename_artifact_path(entry["artifact_path"])}
        migrated_entries[new_key] = new_entry
    return {**payload, "entries": migrated_entries}


def _episode_entries(project_dir: Path, project: dict[str, Any]) -> list[tuple[Path | None, int]]:
    episodes = project.get("episodes")
    if episodes is None:
        return []
    if not isinstance(episodes, list):
        raise ValueError("project.episodes 必须是数组")
    result: list[tuple[Path | None, int]] = []
    for index, entry in enumerate(episodes):
        if not isinstance(entry, dict):
            raise ValueError(f"project.episodes[{index}] 必须是对象")
        episode = entry.get("episode")
        if not isinstance(episode, int) or isinstance(episode, bool) or episode <= 0:
            raise ValueError(f"project.episodes[{index}].episode 必须是正整数")
        script_file = entry.get("script_file")
        script_path = safe_join(project_dir, script_file) if isinstance(script_file, str) and script_file else None
        result.append((script_path, episode))
    return result


def _readable_object(path: Path, label: str) -> dict[str, Any] | None:
    if path.is_symlink():
        raise ValueError(f"{label} 不是普通文件")
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"{label} 不是普通文件")
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} 必须是对象")
    return payload


def _plan_draft_renames(project_dir: Path, episodes: list[tuple[Path | None, int]]) -> list[tuple[Path, Path]]:
    """List draft renames, rejecting occupied destinations without overwriting content."""

    renames: list[tuple[Path, Path]] = []
    for _script_path, episode in episodes:
        drafts_dir = project_dir / "drafts" / f"episode_{episode}"
        if not drafts_dir.is_dir():
            continue
        for old_name, new_name in DRAFT_FILE_RENAMES.items():
            source = drafts_dir / old_name
            if source.is_symlink():
                raise ProjectMigrationError(f"{old_name} 不是普通文件", episode=episode, file=old_name)
            if not source.is_file():
                continue
            target = drafts_dir / new_name
            if target.exists():
                raise ProjectMigrationError(
                    f"{old_name} 与 {new_name} 同时存在，无法判定保留哪一个",
                    episode=episode,
                    file=old_name,
                )
            renames.append((source, target))
    return renames


def plan_script_plan_draft_renames(project_dir: Path) -> tuple[tuple[Path, Path], ...]:
    """Read-only draft-rename planning for migration preflights needing new names before renaming.

    v7→v8 activation uses current code resolving new draft names but precedes this migration. Rename
    first or read old paths during planning, otherwise activation considers drafts absent and skips
    registration. Separate probing/persistence lets callers combine both preflights before writing.
    Return empty tuples if new names are already present."""

    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return ()
    project = load_json(project_file)
    if not isinstance(project, dict):
        raise ValueError("project.json 必须是对象")
    return tuple(_plan_draft_renames(project_dir, _episode_entries(project_dir, project)))


def pending_draft_rename_map(renames: Sequence[tuple[Path, Path]], project_dir: Path) -> dict[str, str]:
    """Convert pending renames into planner canonical-relative-path → current-relative-path mappings.

    Rename pairs point current → canonical; mappings reverse them because planning addresses canonical
    paths while reading old, unrenamed files. Define direction only here."""

    return {
        target.relative_to(project_dir).as_posix(): source.relative_to(project_dir).as_posix()
        for source, target in renames
    }


def apply_script_plan_draft_renames(renames: Sequence[tuple[Path, Path]], *, from_version: int) -> None:
    """Apply :func:`plan_script_plan_draft_renames`: back up all sources, then rename individually."""

    for source, _target in renames:
        ensure_versioned_backup(source, from_version)
    for source, target in renames:
        source.replace(target)


def migrate_v9_to_v10(project_dir: Path) -> None:
    """Single startup/archive entry point through ``migrate_project_dir``."""

    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return
    project = load_json(project_file)
    if not isinstance(project, dict):
        raise ValueError("project.json 必须是对象")
    if int(project.get("schema_version") or 0) >= _TARGET_VERSION:
        return

    # Everything below is read-only: corruption precedes any backup/business write.
    episodes = _episode_entries(project_dir, project)
    draft_renames = _plan_draft_renames(project_dir, episodes)

    migrated_project = copy.deepcopy(project)
    raw_episodes = migrated_project.get("episodes")
    if isinstance(raw_episodes, list):
        migrated_project["episodes"] = [
            _rename_fields(entry, _EPISODE_FIELD_RENAMES, f"project.episodes[{index}]")
            if isinstance(entry, dict)
            else entry
            for index, entry in enumerate(raw_episodes)
        ]

    plans: list[tuple[Path, dict[str, Any]]] = []
    for script_path, episode in episodes:
        if script_path is None:
            continue
        label = script_path.name
        try:
            script = _readable_object(script_path, f"剧本 {label}")
        except ValueError as exc:
            raise ProjectMigrationError(str(exc), episode=episode, file=label) from exc
        if script is None:
            continue
        metadata = script.get("metadata")
        if not isinstance(metadata, dict):
            continue
        try:
            renamed = _rename_fields(metadata, _SCRIPT_METADATA_FIELD_RENAMES, f"剧本 {label}.metadata")
        except ValueError as exc:
            raise ProjectMigrationError(str(exc), episode=episode, file=label) from exc
        if renamed != metadata:
            plans.append((script_path, {**script, "metadata": renamed}))

    manifest_file = project_dir / _MANIFEST_FILENAME
    try:
        manifest = _readable_object(manifest_file, "产物清单")
    except ValueError as exc:
        raise ProjectMigrationError(str(exc), file=_MANIFEST_FILENAME) from exc
    if manifest is not None:
        try:
            plans.append((manifest_file, migrate_manifest(manifest)))
        except ValueError as exc:
            raise ProjectMigrationError(str(exc), file=_MANIFEST_FILENAME) from exc

    # Touch disk only after all preflights pass; complete backups of every rewritten/renamed file before writing.
    for path, _payload in [*plans, (project_file, project)]:
        ensure_versioned_backup(path, 9)
    apply_script_plan_draft_renames(draft_renames, from_version=9)
    for path, payload in plans:
        atomic_write_json(path, payload)

    migrated_project["schema_version"] = _TARGET_VERSION
    atomic_write_json(project_file, migrated_project)


__all__ = [
    "DRAFT_FILE_RENAMES",
    "apply_script_plan_draft_renames",
    "migrate_manifest",
    "migrate_v9_to_v10",
    "pending_draft_rename_map",
    "plan_script_plan_draft_renames",
]
