"""Atomic paid-video history and Artifact Manifest selection.

This module owns the commit decision only.  Callers supply a current-basis
projection and the guard that serializes that projection with script edits.
Provider execution, prompt rendering, thumbnail extraction, and post-production
remain outside this boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any

from lib.artifact_manifest import (
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifest,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.version_manager import PaidVersionCommit, VersionManager

SelectionGuard = Callable[[], AbstractContextManager[object]]
CurrentBasisResolver = Callable[[Mapping[str, Any]], ArtifactBasisDescriptor | None]


def commit_paid_video_artifact(
    *,
    project_path: Path,
    versions: VersionManager,
    resource_type: str,
    resource_id: str,
    prompt: str,
    staged_file: Path,
    current_file: Path,
    duration_seconds: int,
    version_metadata: Mapping[str, Any],
    resolve_current_basis: CurrentBasisResolver,
    selection_guard: SelectionGuard | None = None,
    capture_prior_manifest: Callable[[ArtifactManifestEntry | None], None] | None = None,
) -> PaidVersionCommit:
    """Record paid output, then select only a provably current typed artifact.

    The selection predicate runs inside ``VersionManager``'s lock and, when a
    caller supplies ``selection_guard``, inside that outer guard as well.  The
    formal file, version pointer, and Manifest registration therefore become one
    guarded transition.  Missing or malformed typed facts are deliberately
    history-only instead of being inferred from a legacy path or prompt.
    """

    metadata = dict(version_metadata)
    episode, frozen_basis = _typed_video_identity(metadata)
    guard_factory = selection_guard or nullcontext

    def _archive_paid_history() -> PaidVersionCommit:
        return versions.commit_staged_paid_version(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            staged_file=staged_file,
            current_file=current_file,
            select_current=False,
            duration_seconds=duration_seconds,
            **metadata,
        )

    try:
        artifact_path = _relative_artifact_path(project_path, current_file)
    except BaseException as failure:
        try:
            _archive_paid_history()
        except BaseException as archive_failure:
            failure.add_note(f"paid video history archival also failed: {archive_failure}")
        raise

    def _select_if_current() -> bool:
        if episode is None or frozen_basis is None:
            return False
        try:
            current_basis = resolve_current_basis(metadata)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            return False
        return current_basis == frozen_basis

    def _register_selected_basis() -> None:
        assert episode is not None
        assert frozen_basis is not None
        adapter = ProjectArtifactManifestAdapter(project_path)
        if capture_prior_manifest is not None:
            capture_prior_manifest(adapter.get_entry(ArtifactKey.episode_video(episode, resource_id)))
        ArtifactManifest(adapter).register_descriptor_transactionally(
            ArtifactKey.episode_video(episode, resource_id),
            artifact_path=artifact_path,
            basis=frozen_basis,
        )

    try:
        with guard_factory():
            return versions.commit_staged_paid_version(
                resource_type=resource_type,
                resource_id=resource_id,
                prompt=prompt,
                staged_file=staged_file,
                current_file=current_file,
                select_current=_select_if_current,
                on_select=_register_selected_basis,
                duration_seconds=duration_seconds,
                **metadata,
            )
    except BaseException as failure:
        if staged_file.is_file():
            try:
                _archive_paid_history()
            except BaseException as archive_failure:
                failure.add_note(f"paid video history archival also failed: {archive_failure}")
        raise


def _typed_video_identity(
    metadata: Mapping[str, Any],
) -> tuple[int | None, ArtifactBasisDescriptor | None]:
    episode = metadata.get("artifact_episode")
    raw_basis = metadata.get("artifact_video_basis")
    if type(episode) is not int or episode < 1 or not isinstance(raw_basis, Mapping):
        return None, None
    try:
        descriptor = ArtifactBasisDescriptor.from_dict(raw_basis)
    except (TypeError, ValueError):
        return None, None
    if descriptor.kind != "artifact-components/video":
        return None, None
    return episode, descriptor


def _relative_artifact_path(project_path: Path, current_file: Path) -> str:
    root = project_path.resolve(strict=True)
    resolved = current_file.resolve(strict=False)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("formal video path must stay inside the project") from exc
    if not relative.parts:
        raise ValueError("formal video path must identify a file")
    return relative.as_posix()


__all__ = ["CurrentBasisResolver", "SelectionGuard", "commit_paid_video_artifact"]
