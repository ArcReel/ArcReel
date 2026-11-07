"""Restore items removed from formal scripts (storyboards/reference units) to original episodes after original predecessors (``docs/adr/0107``).

Deletion lives in :mod:`lib.project.script_entry_cleanup`. Find predecessors in pre-edit script snapshots:
insert after surviving predecessors, first if originally first, or at episode end if predecessors are gone.

Allocate IDs without reuse (``docs/adr/0090``): always issue beyond episode historical maxima, never
reuse originals even if free, preventing old timeline clips/trims from reconnecting. Restore media/history
under new IDs; storyboard/video/narration registrations come from migrated temporary projects and
follow normal currency checks. Items leave original grids and plan as ordinary storyboards. Subtitles/
presentations are derived and not restored. If parent episodes are also in trash, restore only with them."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifacts.artifact_registration import register_artifact_entries_atomically
from lib.artifacts.formal_write import formal_write_transaction
from lib.artifacts.version_manager import VersionManager
from lib.episode.episode_trash import require_live_episode
from lib.infra.api_errors import ConflictError
from lib.project.project_change_hints import emit_project_change_hint
from lib.project.project_manager import ProjectManager
from lib.project.script_entry_cleanup import (
    ENTRY_MEDIA_RESOURCE_TYPES,
    SCRIPT_ENTRY_KIND,
    entry_derived_paths,
    entry_files,
    entry_media_paths,
)
from lib.project.trash_restore import Landing, Restoration
from lib.script.script_editor import ScriptEditError, new_item_id, resolve_items

__all__ = ["SCRIPT_ENTRY_KIND", "take_script_entry"]

#: First three ``ArtifactKey.episode_resource_artifacts`` restored with media: storyboard, video, narration.
_MEDIA_ARTIFACT_COUNT = 3


def take_script_entry(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """Extract item, predecessor, files, history, and registrations by ID from migrated temporary projects."""
    entry_id, script_file, episode = str(obj.get("id")), str(obj.get("script_file")), obj.get("episode")
    try:
        staged_script = json.loads((staged / "scripts" / script_file).read_text(encoding="utf-8"))
        items, id_field, kind = resolve_items(staged_script)
    except (OSError, ValueError, ScriptEditError) as exc:
        raise _failed(entry_id) from exc
    index = next((i for i, item in enumerate(items) if isinstance(item, dict) and item.get(id_field) == entry_id), None)
    if index is None or not isinstance(episode, int):
        raise _failed(entry_id)
    entry: dict[str, Any] = items[index]
    previous = items[index - 1].get(id_field) if index > 0 and isinstance(items[index - 1], dict) else None
    files, _history = entry_files(staged, episode, entry_id)
    # Restore storyboard/video/narration registrations only; subtitles/presentation and their registrations are excluded.
    media_keys = ArtifactKey.episode_resource_artifacts(episode, entry_id)[:_MEDIA_ARTIFACT_COUNT]
    staged_claims = ProjectArtifactManifestAdapter(staged).snapshot_entries()
    claims = {key: staged_claims[key] for key in media_keys if key in staged_claims}
    projects = ProjectManager.for_project_dir(project_dir)
    versions = VersionManager(project_dir)
    # Degrade for unavailable objects (permanently deleted episode), or reject trashed episodes; recheck under landing locks.
    require_live_episode(project_dir, episode, entry_id)
    landed: dict[str, str] = {}
    landed_paths: dict[str, str] = {}

    def land(landing: Landing) -> Mapping[str, Any]:
        require_live_episode(project_dir, episode, entry_id)

        def insert(script: dict) -> None:
            current, current_id_field, current_kind = resolve_items(script)
            if current_kind != kind or current_id_field != id_field:
                raise _failed(entry_id)
            taken = {item.get(id_field) for item in current if isinstance(item, dict)}

            def free(candidate: str) -> bool:
                return (
                    candidate not in taken
                    and all(
                        landing.free(rel)
                        for rel in (*entry_media_paths(candidate), *entry_derived_paths(episode, candidate))
                    )
                    and not versions.resource_records((t, candidate) for t in ENTRY_MEDIA_RESOURCE_TYPES)
                )

            floor = projects.item_id_floor(projects.load_project(project_dir.name), script, episode)
            new_id = _new_id(script, kind, id_field, free, floor)
            placed, relink = _rename(staged, episode, entry_id, new_id, files)
            landed_paths.update(relink)
            for source, rel in placed.items():
                landing.place(staged / source, rel, original=source)
            history = VersionManager(staged).resource_records((t, new_id) for t in ENTRY_MEDIA_RESOURCE_TYPES)
            versions.attach_resources(history)
            restored = _relinked(entry, relink) | {id_field: new_id}
            ids = [item.get(id_field) if isinstance(item, dict) else None for item in current]
            position = ids.index(previous) + 1 if previous in ids else (0 if previous is None else len(current))
            script[kind] = [*current[:position], restored, *current[position:]]
            landed["id"] = new_id

        script_path = project_dir / "scripts" / script_file
        with formal_write_transaction(script_path, project_dir / ProjectManager.PROJECT_FILE, versions.versions_file):
            projects.update_script_locked(project_dir.name, script_file, insert)
            if claims:
                # Backfill registrations under new IDs from predeletion, migrated manifests; normal currency checks apply,
                # reading stale when new-ID inputs differ from predeletion.
                renamed_keys = dict(
                    zip(media_keys, ArtifactKey.episode_resource_artifacts(episode, landed["id"]), strict=False)
                )
                register_artifact_entries_atomically(
                    project_dir,
                    {
                        renamed_keys[key]: ArtifactManifestEntry(
                            landed_paths.get(claim.artifact_path, claim.artifact_path), claim.basis_digest
                        )
                        for key, claim in claims.items()
                    },
                )
        return {**obj, "id": landed["id"]}

    return Restoration(
        land=land,
        locks=lambda: projects.script_lock(project_dir.name, script_file),
        after=lambda: emit_project_change_hint(
            project_dir.name, changed_paths=[f"scripts/{script_file}", ProjectManager.PROJECT_FILE]
        ),
    )


def _new_id(script: dict, kind: str, id_field: str, free: Callable[[str], bool], floor: int) -> str:
    """Allocate as for new items beyond episode historical maxima, skipping IDs still occupied by media/history."""
    probe = {**script, kind: list(script.get(kind) or [])}
    while not free(candidate := new_item_id(probe, floor=floor)):
        probe[kind].append({id_field: candidate})
    return candidate


def _rename(
    staged: Path, episode: int, old_id: str, new_id: str, files: list[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Return ``(要加回的文件：临时项目内路径 → 落点, 条目字段里要改指的路径)``.

    Rename media/snapshots to new IDs, excluding subtitles/presentation. Repoint all old-ID item paths,
    even absent files, to avoid referring to old IDs."""
    staged_versions = VersionManager(staged)
    # Snapshots contain whole deletion-time projects; new IDs may then belong to others, whose history must not return with this record. Remove it first.
    staged_versions.detach_resources((resource_type, new_id) for resource_type in ENTRY_MEDIA_RESOURCE_TYPES)
    for resource_type in ENTRY_MEDIA_RESOURCE_TYPES:
        # Rename in temporary projects, updating snapshots/history to new IDs before adding whole.
        staged_versions.rename_resource(resource_type, old_id, new_id)
    renamed = dict(zip(entry_media_paths(old_id), entry_media_paths(new_id), strict=True))
    new_files, _history = entry_files(staged, episode, new_id)
    snapshots = {rel: rel for rel in new_files if rel.startswith("versions/")}
    return {rel: renamed[rel] for rel in files if rel in renamed} | snapshots, renamed


def _relinked(entry: Mapping[str, Any], relink: Mapping[str, str]) -> dict[str, Any]:
    """Repoint old-ID artifact/end-frame paths to new IDs; reidentified items no longer belong to their former grids."""
    restored = json.loads(json.dumps(entry))
    assets = restored.get("generated_assets")
    if relink and isinstance(assets, dict):
        # Grid members still use old IDs; retaining grid fields makes planners skip this storyboard image for missing grid targets.
        assets.update(grid_id=None, grid_cell_index=None)
    for holder in (restored, assets if isinstance(assets, dict) else {}):
        for key, value in holder.items():
            if isinstance(value, str) and value in relink:
                holder[key] = relink[value]
    return restored


def _failed(identity: str) -> ConflictError:
    return ConflictError("trash_restore_failed", id=identity)
