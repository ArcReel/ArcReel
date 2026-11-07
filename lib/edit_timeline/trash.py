"""Editing timelines in project trash: deletion creates a deletion record with a structural snapshot; restoration follows landing rules.

A record holds the timeline document and final-video/Jianying-draft files under ``renders/``, including
render records. Reuse the original ID if free (no same-ID document or render directory); take rendered
artifact registrations from the migrated temporary project and apply normal currency checks. If the
ID is occupied, assign a new ID, restore renders under it without registrations. Append " (n)" when
the display name is occupied within the episode."""

from __future__ import annotations

import contextlib
import os
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import ArtifactKind, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifacts.artifact_registration import forget_timeline_render_artifacts, register_artifact_entries_atomically
from lib.artifacts.rendered_artifact import timeline_renders_dir
from lib.edit_timeline.model import TIMELINE_NAME_MAX_LENGTH, EditTimelineDocument
from lib.edit_timeline.store import (
    EDIT_TIMELINES_DIRNAME,
    EditTimelineStore,
    document_relpath,
    read_timeline_document,
)
from lib.episode.episode_trash import require_live_episode
from lib.infra.api_errors import ConflictError
from lib.project import project_trash
from lib.project.project_manager import ProjectManager
from lib.project.trash_restore import Landing, Restoration

__all__ = ["EDIT_TIMELINE_KIND", "delete_edit_timeline", "take_edit_timeline"]

EDIT_TIMELINE_KIND = "edit_timeline"
_RENDER_KINDS = frozenset({ArtifactKind.EPISODE_FINAL_CUT, ArtifactKind.EPISODE_JIANYING_DRAFT})


def delete_edit_timeline(project_dir: Path, document: EditTimelineDocument) -> project_trash.TrashRecord:
    """Move a timeline and renders into project trash; callers hold its episode lock followed by the project lock."""
    renders = timeline_renders_dir(document.episode, document.id)
    files = [document_relpath(document.episode, document.id), *_files_under(project_dir, renders)]
    record = project_trash.record_deletion(
        project_dir,
        {"kind": EDIT_TIMELINE_KIND, "id": document.id, "episode": document.episode, "name": document.name},
        files,
        snapshot=True,
    )
    # Timeline IDs are never reused; final videos/Jianying drafts belong to them. Without the timeline, registrations have no owner.
    forget_timeline_render_artifacts(project_dir, document.id)
    _remove_empty_dirs(project_dir / renders)
    return record


def take_edit_timeline(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """Take a timeline document, renders, and their manifest entries from the migrated temporary project by timeline ID."""
    timeline_id = str(obj.get("id"))
    document = next(
        (
            found
            for path in sorted(staged.glob(f"{EDIT_TIMELINES_DIRNAME}/episode_*/{timeline_id}.json"))
            if (found := read_timeline_document(staged, _episode_of(path), timeline_id)) is not None
        ),
        None,
    )
    if document is None:
        raise ConflictError("trash_restore_failed", id=timeline_id)
    projects = ProjectManager.for_project_dir(project_dir)

    def require_episode() -> None:
        # Restored timelines cannot open without their episode: if it is in project trash, restore only with that episode; otherwise degrade to object unavailable.
        require_live_episode(project_dir, document.episode, timeline_id)

    # Recheck under landing locks; an episode deleted meanwhile fails restoration and preserves the record.
    require_episode()
    staged_renders = timeline_renders_dir(document.episode, document.id)
    render_files = _files_under(staged, staged_renders)
    claims = {
        key: entry
        for key, entry in ProjectArtifactManifestAdapter(staged).snapshot_entries().items()
        if key.kind in _RENDER_KINDS and key.components[1] == document.id
    }
    store = EditTimelineStore(projects, project_dir.name)

    def land(landing: Landing) -> Mapping[str, Any]:
        require_episode()
        taken_ids = {existing.id for existing in store.list_documents()}
        landed_id = document.id
        while (
            landed_id in taken_ids
            or not landing.free(document_relpath(document.episode, landed_id))
            or not landing.free(timeline_renders_dir(document.episode, landed_id))
        ):
            landed_id = f"tl-{secrets.token_hex(4)}"
        names = {existing.name.casefold() for existing in store.list_documents(document.episode)}
        landed = document.model_copy(update={"id": landed_id, "name": _free_name(document.name, names)})
        landing.write_json(
            document_relpath(landed.episode, landed.id),
            landed.model_dump(mode="json"),
            original=document_relpath(document.episode, document.id),
        )
        renders = timeline_renders_dir(landed.episode, landed.id)
        for rel in render_files:
            landing.place(staged / rel, renders + rel[len(staged_renders) :], original=rel)
        if landed.id == document.id and claims:
            # Backfill entries from the pre-deletion (migrated) manifest, preserving basis digests; later input changes still read outdated normally.
            register_artifact_entries_atomically(
                project_dir,
                {key: ArtifactManifestEntry(entry.artifact_path, entry.basis_digest) for key, entry in claims.items()},
            )
        return {**obj, "id": landed.id, "name": landed.name}

    return Restoration(
        land=land,
        locks=lambda: store.locked_episode(document.episode),
        after=lambda: store.changed(document.episode),
    )


def _episode_of(path: Path) -> int:
    number = path.parent.name.removeprefix("episode_")
    return int(number) if number.isdigit() else 0


def _free_name(name: str, taken: set[str]) -> str:
    candidate, n = name, 0
    while candidate.casefold() in taken:
        n += 1
        suffix = f" ({n})"
        candidate = name[: TIMELINE_NAME_MAX_LENGTH - len(suffix)].rstrip() + suffix
    return candidate


def _files_under(root: Path, rel_dir: str) -> list[str]:
    """Regular files under ``rel_dir`` as project-relative POSIX paths, without following symlinks."""
    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root / rel_dir):
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_file() and not path.is_symlink():
                found.append(path.relative_to(root).as_posix())
    return sorted(found)


def _remove_empty_dirs(directory: Path) -> None:
    for dirpath, _dirnames, _filenames in os.walk(directory, topdown=False):
        # Leave directories containing unmoved entries such as symlinks.
        with contextlib.suppress(OSError):
            # hard-delete: derived -- files moved to project trash, leaving only an empty directory
            os.rmdir(dirpath)
