"""Typed media-version restore coordinated with script and Artifact Manifest state."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import (
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifest,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.artifact_version_provenance import (
    TypedMediaVersionTarget,
    is_typed_media_resource,
    is_uploaded_video_version,
    parse_typed_media_version_target,
    uploaded_video_version_basis,
)
from lib.artifacts.version_manager import VersionManager
from lib.infra.api_errors import NotFoundError
from lib.infra.content_digest import sha256_file
from lib.project.project_manager import ProjectManager, resolve_episode_script_binding
from lib.project.video_unit_owner import resolve_video_unit_owner
from lib.script.script_editor import resolve_items
from server.services.tasks.reference_video_tasks import apply_unit_video_assets

_TYPED_MEDIA_ARTIFACT_KEYS: dict[str, Callable[[int, str], ArtifactKey]] = {
    "audio": ArtifactKey.episode_audio,
    "videos": ArtifactKey.episode_video,
    "reference_videos": ArtifactKey.episode_video,
}


def is_typed_media_restore_resource(resource_type: str) -> bool:
    return is_typed_media_resource(resource_type)


def is_typed_media_version_restorable(resource_type: str, record: Mapping[str, Any]) -> bool:
    """Whether the version has a supported restore source."""

    if not is_typed_media_restore_resource(resource_type) or is_uploaded_video_version(resource_type, record):
        return True
    try:
        parse_typed_media_version_record(resource_type, record)
    except (TypeError, ValueError):
        return False
    return True


TypedMediaRestoreTarget = TypedMediaVersionTarget


@dataclass(frozen=True, slots=True)
class UploadedVideoRestoreTarget:
    episode: int
    script_file: str
    created_at: str | None


def get_typed_media_restore_target(
    versions: VersionManager,
    *,
    project_manager: ProjectManager,
    project_name: str,
    resource_type: str,
    resource_id: str,
    version: int,
) -> TypedMediaRestoreTarget | UploadedVideoRestoreTarget:
    """Resolve the admission identity for an uploaded or generated media version."""

    if not is_typed_media_restore_resource(resource_type):
        raise ValueError(f"resource type does not carry typed artifact restore metadata: {resource_type}")
    records = versions.get_versions(resource_type, resource_id).get("versions", [])
    record = next(
        (candidate for candidate in records if isinstance(candidate, Mapping) and candidate.get("version") == version),
        None,
    )
    if record is None:
        raise NotFoundError("version_not_found", version=version)
    if is_uploaded_video_version(resource_type, record):
        episode, script_file = resolve_video_unit_owner(
            project_manager,
            project_name=project_name,
            project=project_manager.load_project(project_name),
            resource_type=resource_type,
            resource_id=resource_id,
        )
        created_at = record.get("created_at")
        return UploadedVideoRestoreTarget(episode, script_file, created_at if isinstance(created_at, str) else None)
    return parse_typed_media_version_record(resource_type, record)


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
    target: TypedMediaRestoreTarget | UploadedVideoRestoreTarget | None = None,
) -> dict[str, Any]:
    """Restore the selected media, script, version pointer and Manifest together."""

    resolved = target or get_typed_media_restore_target(
        versions,
        project_manager=project_manager,
        project_name=project_name,
        resource_type=resource_type,
        resource_id=resource_id,
        version=version,
    )
    restored: list[dict[str, Any]] = []

    def _same_script(project: dict[str, Any]) -> str:
        if isinstance(resolved, UploadedVideoRestoreTarget):
            owner = resolve_video_unit_owner(
                project_manager,
                project_name=project_name,
                project=project,
                resource_type=resource_type,
                resource_id=resource_id,
            )
            if owner != (resolved.episode, resolved.script_file):
                raise ValueError("upload owner changed during video restore")
            return resolved.script_file
        current_binding = resolve_episode_script_binding(project, resolved.episode, resolved.script_file)
        if current_binding is None:
            raise ValueError("typed artifact version no longer matches the episode script binding")
        return current_binding

    def _restore_and_register(_script_path: Path) -> None:
        def _register(record: dict[str, Any]) -> None:
            """Use the installed bytes after VersionManager has admitted the snapshot."""

            if is_uploaded_video_version(resource_type, record) != isinstance(resolved, UploadedVideoRestoreTarget):
                raise ValueError("version source changed during restore")
            if isinstance(resolved, UploadedVideoRestoreTarget):
                basis = ArtifactBasisDescriptor.from_basis(
                    uploaded_video_version_basis(record, snapshot_digest=sha256_file(current_file))
                )
            else:
                committed_target = parse_typed_media_version_record(resource_type, record)
                if committed_target != resolved:
                    raise RuntimeError("typed artifact version metadata changed during restore")
                basis = resolved.basis
            key = _TYPED_MEDIA_ARTIFACT_KEYS[resource_type](resolved.episode, resource_id)
            ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register_descriptor_transactionally(
                key,
                artifact_path=artifact_path,
                basis=basis,
            )

        restored.append(
            versions.restore_version(
                resource_type,
                resource_id,
                version,
                current_file,
                on_restore=_register,
            )
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
            created_at=resolved.created_at,
        )

    if not restored:
        raise RuntimeError("typed artifact restore completed without selecting a version")
    return restored[0]


def parse_typed_media_version_record(
    resource_type: str,
    record: Mapping[str, Any],
) -> TypedMediaRestoreTarget:
    """Validate typed provenance carried by a media version record.

    Restore and presentation adapters share this parser so neither can accept a
    history record that the other considers unverifiable.
    """

    return parse_typed_media_version_target(resource_type, record)


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
    "UploadedVideoRestoreTarget",
    "get_typed_media_restore_target",
    "is_typed_media_restore_resource",
    "is_typed_media_version_restorable",
    "parse_typed_media_version_record",
    "restore_typed_media_version",
]
