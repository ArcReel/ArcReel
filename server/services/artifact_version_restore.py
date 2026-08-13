"""Typed media-version restore coordinated with script and Artifact Manifest state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.api_errors import NotFoundError
from lib.artifact_manifest import (
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifest,
    ProjectArtifactManifestAdapter,
)
from lib.project_manager import ProjectManager, find_episode
from lib.script_editor import resolve_items
from lib.version_manager import VersionManager
from server.services.reference_video_tasks import apply_unit_video_assets

_TYPED_MEDIA_RESOURCE_TYPES = frozenset({"audio", "videos", "reference_videos"})


@dataclass(frozen=True, slots=True)
class TypedMediaRestoreTarget:
    episode: int
    script_file: str
    basis: ArtifactBasisDescriptor
    created_at: str | None


def get_typed_media_restore_target(
    versions: VersionManager,
    *,
    resource_type: str,
    resource_id: str,
    version: int,
) -> TypedMediaRestoreTarget:
    """Read and validate the complete typed identity carried by one version."""

    if resource_type not in _TYPED_MEDIA_RESOURCE_TYPES:
        raise ValueError(f"resource type does not carry typed artifact restore metadata: {resource_type}")
    records = versions.get_versions(resource_type, resource_id).get("versions", [])
    record = next(
        (candidate for candidate in records if isinstance(candidate, Mapping) and candidate.get("version") == version),
        None,
    )
    if record is None:
        raise NotFoundError("version_not_found", version=version)
    return _target_from_record(resource_type, record)


def restore_typed_media_version(
    *,
    project_manager: ProjectManager,
    project_name: str,
    project_path: Path,
    versions: VersionManager,
    resource_type: str,
    resource_id: str,
    version: int,
    current_file: Path,
    artifact_path: str,
) -> dict[str, Any]:
    """Restore a typed version as one script/media/pointer/Manifest transition.

    Historical versions without a complete descriptor are rejected.  Their
    provenance cannot be reconstructed from a path, prompt, or current project
    state without making an unprovable selection claim.
    """

    target = get_typed_media_restore_target(
        versions,
        resource_type=resource_type,
        resource_id=resource_id,
        version=version,
    )
    restored: dict[str, Any] | None = None

    def _same_script(project: dict[str, Any]) -> str:
        episode_entry = find_episode(project, target.episode)
        current_binding = episode_entry.get("script_file") if isinstance(episode_entry, dict) else None
        if not isinstance(current_binding, str) or (
            ProjectManager.normalize_script_filename(current_binding)
            != ProjectManager.normalize_script_filename(target.script_file)
        ):
            raise ValueError("typed artifact version no longer matches the episode script binding")
        return current_binding

    def _restore_and_register(_script_path: Path) -> None:
        nonlocal restored

        def _register(record: dict[str, Any]) -> None:
            committed_target = _target_from_record(resource_type, record)
            if committed_target != target:
                raise RuntimeError("typed artifact version metadata changed during restore")
            key = (
                ArtifactKey.episode_audio(target.episode, resource_id)
                if resource_type == "audio"
                else ArtifactKey.episode_video(target.episode, resource_id)
            )
            ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register_descriptor_transactionally(
                key,
                artifact_path=artifact_path,
                basis=target.basis,
            )

        restored = versions.restore_version(
            resource_type,
            resource_id,
            version,
            current_file,
            on_restore=_register,
        )

    with project_manager.locked_episode_script(
        project_name,
        _same_script,
        validate=False,
        on_commit=_restore_and_register,
    ) as script:
        _apply_restored_asset(
            project_manager=project_manager,
            script=script,
            resource_type=resource_type,
            resource_id=resource_id,
            artifact_path=artifact_path,
            created_at=target.created_at,
        )

    if restored is None:
        raise RuntimeError("typed artifact restore completed without selecting a version")
    return restored


def _target_from_record(resource_type: str, record: Mapping[str, Any]) -> TypedMediaRestoreTarget:
    episode = record.get("artifact_episode")
    script_file = record.get("execution_script_file")
    basis_field = "artifact_audio_basis" if resource_type == "audio" else "artifact_video_basis"
    raw_basis = record.get(basis_field)
    if type(episode) is not int or episode < 1 or not isinstance(script_file, str) or not script_file.strip():
        raise ValueError("version does not contain complete typed artifact metadata")
    try:
        basis = ArtifactBasisDescriptor.from_dict(raw_basis)
    except (TypeError, ValueError) as exc:
        raise ValueError("version does not contain complete typed artifact metadata") from exc
    expected_kind = "narration-delivery/tts-audio" if resource_type == "audio" else "artifact-components/video"
    if basis.kind != expected_kind:
        raise ValueError("version does not contain complete typed artifact metadata")
    created_at = record.get("created_at")
    return TypedMediaRestoreTarget(
        episode=episode,
        script_file=script_file,
        basis=basis,
        created_at=created_at if isinstance(created_at, str) else None,
    )


def _apply_restored_asset(
    *,
    project_manager: ProjectManager,
    script: dict[str, Any],
    resource_type: str,
    resource_id: str,
    artifact_path: str,
    created_at: str | None,
) -> None:
    if resource_type == "reference_videos":
        apply_unit_video_assets(
            script,
            resource_id,
            video_uri=None,
            thumb_rel=None,
            generated_at=created_at,
        )
        return

    items, id_field, _kind = resolve_items(script)
    item = next(
        (
            candidate
            for candidate in items
            if isinstance(candidate, dict) and str(candidate.get(id_field)) == str(resource_id)
        ),
        None,
    )
    if item is None:
        raise KeyError(resource_id)
    assets = item.get("generated_assets")
    if not isinstance(assets, dict):
        assets = ProjectManager.create_generated_assets(str(script.get("content_mode") or "narration"))
        item["generated_assets"] = assets
    if resource_type == "audio":
        assets["narration_audio"] = artifact_path
    else:
        assets["video_clip"] = artifact_path
        assets["video_uri"] = None
        assets["video_thumbnail"] = None
    project_manager.update_scene_status(item)


__all__ = [
    "TypedMediaRestoreTarget",
    "get_typed_media_restore_target",
    "restore_typed_media_version",
]
