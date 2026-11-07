"""Move planning drafts through project trash: deleting unconfirmed formal plans and repair/editable drafts creates a record with a structural snapshot.

Restoration requires the owning episode to exist. If the original path is free, restore it and backfill pre-deletion
(post-migration) plan registration; otherwise add “<original name> (n)” as an unreferenced file without registration.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifacts.artifact_registration import register_artifact_entries_atomically
from lib.artifacts.formal_write import project_metadata_lock
from lib.infra.api_errors import ConflictError
from lib.project import project_trash
from lib.project.project_activity import record_project_activity
from lib.project.project_change_hints import emit_project_change_hint
from lib.project.project_manager import ProjectManager, find_episode
from lib.project.trash_restore import Landing, Restoration
from lib.script.draft_quarantine import quarantine_path

__all__ = ["PLANNING_DRAFT_KIND", "discard_quarantine", "take_planning_draft", "trash_planning_draft"]

PLANNING_DRAFT_KIND = "planning_draft"


def trash_planning_draft(project_dir: Path, episode: int, path: Path) -> project_trash.TrashRecord | None:
    """Move episode ``episode`` planning draft ``path`` into one deletion record; None for nonregular files. Caller holds the project lock."""
    if path.is_symlink() or not path.is_file():
        return None
    rel = path.relative_to(project_dir).as_posix()
    return project_trash.record_deletion(
        project_dir, {"kind": PLANNING_DRAFT_KIND, "path": rel, "episode": episode}, [rel], snapshot=True
    )


def discard_quarantine(project_path: Path, episode: int, kind: str) -> bool:
    """Discard a draft abandoned by a creator/Agent into project trash; return actual presence and advance project activity if present.

    Caller holds the draft lock, not the project lock.
    """
    with project_metadata_lock(project_path):
        record = trash_planning_draft(project_path, episode, quarantine_path(project_path, episode, kind))
    if record is None:
        return False
    record_project_activity(project_path)
    return True


def take_planning_draft(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """Take the draft and its plan registration from a migrated temporary project by original path."""
    rel, episode = str(obj.get("path")), int(obj.get("episode") or 0)
    staged_file = staged / rel
    if staged_file.is_symlink() or not staged_file.is_file():
        raise ConflictError("trash_restore_failed", id=rel)
    key = ArtifactKey.episode_script_plan(episode)
    claim = ProjectArtifactManifestAdapter(staged).snapshot_entries().get(key)
    projects = ProjectManager.for_project_dir(project_dir)
    landed: list[str] = []

    def require_episode() -> None:
        if find_episode(projects.load_project(project_dir.name), episode) is None:
            # Owning episode was deleted; the restored draft has no owner.
            raise ConflictError("trash_restore_failed", id=rel)

    # Treat errors here as unavailable objects; recheck under lock on restore. Intervening episode deletion fails restore, retaining the record.
    require_episode()

    def land(landing: Landing) -> Mapping[str, Any]:
        require_episode()
        target = rel
        path = PurePosixPath(rel)
        n = 0
        while not landing.free(target):
            n += 1
            target = path.with_name(f"{path.stem} ({n}){path.suffix}").as_posix()
        landing.place(staged_file, target, original=rel)
        landed.append(target)
        if target == rel and claim is not None and claim.artifact_path == rel:
            # Backfill with the same basis digest; later input changes still read as stale.
            register_artifact_entries_atomically(
                project_dir, {key: ArtifactManifestEntry(claim.artifact_path, claim.basis_digest)}
            )
        return {**obj, "path": target}

    return Restoration(
        land=land,
        locks=lambda: projects.file_lock(project_dir / rel),
        after=lambda: emit_project_change_hint(project_dir.name, changed_paths=landed),
    )
