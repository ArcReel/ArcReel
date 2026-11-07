"""Restore deleted episodes (``docs/adr/0107``): reuse IDs, always append broadcast order as standalone-source episodes.

Predeletion episode sources become standalone material; whole-source splits release original ranges as unsplit. Sourceless episodes remain sourceless.
Restore scripts/planning drafts/editing timelines/final videos/grids/media to original paths. Version histories/artifact registrations come from migrated temporary projects;
normal artifact currency applies, with changed postdeletion inputs reading stale.

Episode-owned timelines/storyboards restore original structures only after parent episodes (:func:`require_live_episode`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifacts.artifact_registration import register_artifact_entries_atomically
from lib.artifacts.formal_write import formal_write_transaction
from lib.artifacts.version_manager import VersionManager
from lib.episode.episode_deletion import EPISODE_KIND, episode_content, episode_trash_record
from lib.episode.episode_paths import episode_source_path
from lib.episode.episode_sources import SOURCE_ORIGIN_FIELD, SourceOrigin
from lib.episode.source_kinds import SOURCE_KIND_FIELD, entry_source_kind, record_episode_kind
from lib.infra.api_errors import ConflictError
from lib.project.project_change_hints import emit_project_change_hint
from lib.project.project_manager import ProjectManager, find_episode
from lib.project.trash_restore import Landing, Restoration, RestoreBlocked

__all__ = ["EPISODE_KIND", "require_live_episode", "take_episode"]


def take_episode(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """Take ledger entries/episode files/version records/artifact registrations by ID from migrated temporary projects."""
    episode = obj.get("id")
    staged_project = json.loads((staged / "project.json").read_text(encoding="utf-8"))
    entry = find_episode(staged_project, episode if isinstance(episode, int) else None)
    if entry is None:
        raise ConflictError("trash_restore_failed", id=str(episode))
    number: int = entry["episode"]
    content = episode_content(staged, staged_project, number)
    claims = {
        key: ArtifactManifestEntry(claim.artifact_path, claim.basis_digest)
        for key, claim in ProjectArtifactManifestAdapter(staged).snapshot_entries().items()
        if key.episode_number == number
    }
    source = episode_source_path(staged, number)
    own = source.is_file() and not source.is_symlink()
    restored = {key: value for key, value in entry.items() if key != "source_range"}
    restored[SOURCE_ORIGIN_FIELD] = (SourceOrigin.OWN if own else SourceOrigin.NONE).value
    kind = entry_source_kind(staged_project, entry)
    if not own:
        restored.pop(SOURCE_KIND_FIELD, None)
    projects = ProjectManager.for_project_dir(project_dir)

    def append(project: dict[str, Any]) -> None:
        if find_episode(project, number) is not None:
            # Never reuse IDs, so original IDs should be free; occupied IDs never merge into other episodes.
            raise ConflictError("trash_restore_failed", id=str(number))
        if own:
            record_episode_kind(project, restored, kind)
        project["episodes"] = [*(project.get("episodes") or []), restored]

    def land(landing: Landing) -> Mapping[str, Any]:
        blocked = next((rel for rel in content.files if not landing.free(rel)), None)
        if blocked is not None:
            raise ConflictError("trash_restore_failed", id=str(number))
        versions = VersionManager(project_dir)
        with formal_write_transaction(project_dir / "project.json", versions.versions_file):
            for rel in content.files:
                landing.place(staged / rel, rel, original=rel)
            versions.attach_resources(content.versions)
            projects.update_project_locked(project_dir.name, append)
            if claims:
                # Backfill registrations from predeletion migrated manifests, keeping basis digests; changed inputs still read stale.
                register_artifact_entries_atomically(project_dir, claims)
        return obj

    return Restoration(
        land=land,
        after=lambda: emit_project_change_hint(project_dir.name, changed_paths=[ProjectManager.PROJECT_FILE]),
    )


def require_live_episode(project_dir: Path, episode: object, identity: str) -> None:
    """Verify live parent episodes before restoring attached objects.

    Episodes still in project trash raise :class:`RestoreBlocked`: restore episodes first, then records, without degradation.
    Absent episodes without deletion records, permanently deleted, raise ``trash_restore_failed`` for unavailable-object degradation.
    """
    number = episode if isinstance(episode, int) and not isinstance(episode, bool) else None
    project = ProjectManager.for_project_dir(project_dir).load_project(project_dir.name)
    if find_episode(project, number) is not None:
        return
    record = episode_trash_record(project_dir, number) if number is not None else None
    if record is None:
        raise ConflictError("trash_restore_failed", id=identity)
    title = record.object.get("title")
    if isinstance(title, str) and title:
        raise RestoreBlocked("trash_restore_needs_episode", name=title)
    raise RestoreBlocked("trash_restore_needs_episode_untitled", episode=number)
