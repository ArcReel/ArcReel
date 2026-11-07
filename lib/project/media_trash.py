"""Project trash for grids, end frames, and character reference audio (``docs/adr/0107``).

All have parents: episodes, script storyboards, or characters. Deletion creates structural-snapshot
records. Missing parents on restoration degrade to files-only; recheck under landing locks, failing
and retaining records if parents disappear meanwhile. Trashed grid-parent episodes do not degrade:
restore episodes before grids (:func:`~lib.episode.episode_trash.require_live_episode`).

- Grids include records, composites, and history snapshots. Free original IDs restore whole, with
  migrated temporary-project history/registrations and normal currency checks; occupied IDs degrade as unavailable objects.
- End frames/audio return to free original locations, otherwise renamed. Reassociate only original-path
  restores whose current parents have no usable file; otherwise restored files remain unreferenced."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifacts.artifact_registration import register_artifact_entries_atomically
from lib.artifacts.formal_write import formal_write_transaction
from lib.artifacts.version_manager import VersionManager
from lib.episode.episode_trash import require_live_episode
from lib.infra.api_errors import ConflictError
from lib.project import project_trash
from lib.project.asset_types import resolve_asset_key
from lib.project.project_change_hints import emit_project_change_hint
from lib.project.project_manager import ProjectManager, is_episode_number
from lib.project.resource_paths import END_FRAME_RESOURCE_TYPE, resource_relative_path
from lib.project.trash_restore import Landing, Restoration
from lib.script.grid.models import GridGeneration
from lib.script.item_excerpt import position_snapshot
from lib.script.storyboard_sequence import find_storyboard_item, get_storyboard_items

__all__ = [
    "END_FRAME_KIND",
    "GRID_KIND",
    "REFERENCE_AUDIO_KIND",
    "take_end_frame",
    "take_grid",
    "take_reference_audio",
    "trash_end_frame",
    "trash_grid",
    "trash_reference_audio",
]

GRID_KIND = "grid"
END_FRAME_KIND = "end_frame"
REFERENCE_AUDIO_KIND = "reference_audio"

_GRID_TYPE = "grids"


# ---------------------------------------------------------------- Deletion


def trash_grid(project_dir: Path, grid_id: str, episode: int | None) -> project_trash.TrashRecord:
    """Trash grid records/composites/history; callers hold project/grid-record locks.

    Empty ``episode`` (unreadable records) has no provable registrations, so move files only."""
    versions = VersionManager(project_dir)
    resource = [(_GRID_TYPE, grid_id)]
    candidates = [
        f"grids/{grid_id}.json",
        resource_relative_path(_GRID_TYPE, grid_id),
        *versions.snapshot_files(versions.resource_records(resource)),
    ]
    files = [rel for rel in dict.fromkeys(candidates) if _is_plain_file(project_dir / rel)]
    # Snapshot first, retaining history/registrations for extraction from migrated temporary projects on restoration.
    record = project_trash.record_deletion(
        project_dir, {"kind": GRID_KIND, "id": grid_id, "episode": episode}, files, snapshot=True
    )
    try:
        with formal_write_transaction(versions.versions_file):
            versions.detach_resources(resource)
            if episode is not None:
                register_artifact_entries_atomically(project_dir, {ArtifactKey.episode_grid(episode, grid_id): None})
    except BaseException:
        project_trash.withdraw(project_dir, record.id)
        raise
    return record


def trash_end_frame(
    project_dir: Path, script_file: str, shot_id: str, script: Mapping[str, Any]
) -> project_trash.TrashRecord:
    """Trash storyboard end-frame snapshots; callers hold script/project locks, having nulled fields in the same critical section.

    ``script`` is the script written there; records retain episode, current position, and body excerpt for
    trash naming. Without integer ``episode``, derive it from canonical ``script_file``."""
    rel = resource_relative_path(END_FRAME_RESOURCE_TYPE, shot_id)
    episode = script.get("episode")
    if not is_episode_number(episode):
        episode = ProjectManager.canonical_script_episode(script_file)
    obj = {
        "kind": END_FRAME_KIND,
        "script_file": script_file,
        "shot_id": shot_id,
        "path": rel,
        "episode": episode,
        **position_snapshot(script, shot_id),
    }
    return project_trash.record_deletion(project_dir, obj, [rel], snapshot=True)


def trash_reference_audio(project_dir: Path, character: str, rel: str) -> project_trash.TrashRecord:
    """Trash character-unreferenced audio; callers hold project locks and already changed fields in the same critical section."""
    obj = {"kind": REFERENCE_AUDIO_KIND, "character": character, "path": rel}
    return project_trash.record_deletion(project_dir, obj, [rel], snapshot=True)


# ---------------------------------------------------------------- Restoration


def take_grid(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """Extract grid records/composites/history/registrations by ID from migrated temporary projects."""
    grid_id = str(obj.get("id"))
    try:
        grid = GridGeneration.from_dict(json.loads((staged / f"grids/{grid_id}.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _failed(grid_id) from exc
    if grid.id != grid_id:
        raise _failed(grid_id)
    resource = [(_GRID_TYPE, grid_id)]
    staged_versions = VersionManager(staged)
    history = staged_versions.resource_records(resource)
    rels = [
        rel
        for rel in dict.fromkeys(
            [
                f"grids/{grid_id}.json",
                resource_relative_path(_GRID_TYPE, grid_id),
                *staged_versions.snapshot_files(history),
            ]
        )
        if _is_plain_file(staged / rel)
    ]
    key = ArtifactKey.episode_grid(grid.episode, grid.id)
    claim = ProjectArtifactManifestAdapter(staged).snapshot_entries().get(key)
    versions = VersionManager(project_dir)

    def require_free() -> None:
        require_live_episode(project_dir, grid.episode, grid_id)
        if any(not _free(project_dir, rel) for rel in rels) or versions.resource_records(resource):
            raise _failed(grid_id)

    # Degrade absent objects (permanently deleted episodes/occupied locations), reject trashed episodes; recheck under landing locks.
    require_free()

    def land(landing: Landing) -> None:
        require_free()
        for rel in rels:
            landing.place(staged / rel, rel, original=rel)
        with formal_write_transaction(versions.versions_file):
            versions.attach_resources(history)
            if claim is not None:
                # Backfill predeletion/migrated registrations unchanged in basis digest; changed later inputs read stale normally.
                register_artifact_entries_atomically(
                    project_dir, {key: ArtifactManifestEntry(claim.artifact_path, claim.basis_digest)}
                )

    return Restoration(land=land)


def take_end_frame(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """Extract storyboard snapshots: restore/reassociate at free original paths; otherwise rename without changing storyboards."""
    rel, script_file, shot_id = str(obj.get("path")), str(obj.get("script_file")), str(obj.get("shot_id"))
    if not _is_plain_file(staged / rel):
        raise _failed(rel)
    projects = ProjectManager.for_project_dir(project_dir)

    def shot_of(script: dict) -> dict:
        items, id_field, *_ = get_storyboard_items(script)
        matched = find_storyboard_item(items, id_field, shot_id)
        if matched is None:
            # Storyboard/parent script deleted; restored end frames have no owner.
            raise _failed(rel)
        return matched[0]

    try:
        shot_of(projects.load_script(project_dir.name, script_file))
    except (FileNotFoundError, ValueError) as exc:
        raise _failed(rel) from exc

    def land(landing: Landing) -> Mapping[str, Any]:
        landed = rel if landing.free(rel) else _new_name(landing, rel)

        def relink(script: dict) -> None:
            shot = shot_of(script)
            if landed == rel:
                shot["end_frame_image"] = rel

        with formal_write_transaction(project_dir / "scripts" / script_file, project_dir / ProjectManager.PROJECT_FILE):
            landing.place(staged / rel, landed, original=rel)
            projects.update_script_locked(project_dir.name, script_file, relink)
        return {**obj, "path": landed}

    return Restoration(
        land=land,
        locks=lambda: projects.script_lock(project_dir.name, script_file),
        after=lambda: emit_project_change_hint(
            project_dir.name, changed_paths=[f"scripts/{script_file}", ProjectManager.PROJECT_FILE]
        ),
    )


def take_reference_audio(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """Extract character audio, using free original paths or new names; reassociate original-path returns only if characters have no usable audio."""
    rel, character = str(obj.get("path")), str(obj.get("character"))
    if not _is_plain_file(staged / rel):
        raise _failed(rel)
    projects = ProjectManager.for_project_dir(project_dir)

    def slot_free() -> bool:
        characters = projects.load_project(project_dir.name).get("characters")
        key = resolve_asset_key(characters, character)
        if key is None or not isinstance(characters, dict):
            # Character deleted; restored audio has no owner.
            raise _failed(rel)
        current = characters[key].get("reference_audio")
        return not (isinstance(current, str) and current and _is_plain_file(project_dir / current))

    slot_free()

    def land(landing: Landing) -> Mapping[str, Any]:
        link = slot_free()
        target = rel if landing.free(rel) else _new_name(landing, rel)

        def relink(project: dict) -> None:
            key = resolve_asset_key(project.get("characters"), character)
            if key is not None:
                ProjectManager._set_character_reference_audio(project, key, target)

        with formal_write_transaction(project_dir / ProjectManager.PROJECT_FILE):
            landing.place(staged / rel, target, original=rel)
            # Renamed files no longer belong to the character's own name, so cannot reassociate as reference audio.
            if link and target == rel:
                projects.update_project_locked(project_dir.name, relink)
        return {**obj, "path": target}

    return Restoration(
        land=land,
        after=lambda: emit_project_change_hint(project_dir.name, changed_paths=[ProjectManager.PROJECT_FILE]),
    )


# ---------------------------------------------------------------- Internal


def _failed(identity: str) -> ConflictError:
    return ConflictError("trash_restore_failed", id=identity)


def _new_name(landing: Landing, rel: str) -> str:
    path = PurePosixPath(rel)
    for n in range(1, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}").as_posix()
        if landing.free(candidate):
            return candidate
    raise FileExistsError(rel)


def _free(project_dir: Path, rel: str) -> bool:
    target = project_dir / rel
    return not (target.exists() or target.is_symlink())


def _is_plain_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()
