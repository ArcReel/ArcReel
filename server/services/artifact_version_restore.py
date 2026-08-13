"""Typed media-version restore coordinated with script and Artifact Manifest state."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
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
from lib.narration_delivery import TtsSynthesisSettings, build_narration_audio_basis_from_canonical_text
from lib.project_manager import ProjectManager, find_episode
from lib.script_editor import resolve_items
from lib.version_manager import VersionManager
from lib.video_artifact_facts import VIDEO_ARTIFACT_RESTORE_BLOCKER_FIELD, VideoArtifactCurrencyFacts
from server.services.reference_video_tasks import apply_unit_video_assets


@dataclass(frozen=True, slots=True)
class _TypedMediaRestoreSpec:
    basis_field: str | None
    basis_kind: str
    artifact_key: Callable[[int, str], ArtifactKey]
    visual_kind: str | None = None


_TYPED_MEDIA_RESTORE_SPECS = {
    "audio": _TypedMediaRestoreSpec(
        basis_field="artifact_audio_basis",
        basis_kind="narration-delivery/tts-audio",
        artifact_key=ArtifactKey.episode_audio,
    ),
    "videos": _TypedMediaRestoreSpec(
        basis_field=None,
        basis_kind="artifact-components/video",
        artifact_key=ArtifactKey.episode_video,
        visual_kind="artifact-visual/video-storyboard",
    ),
    "reference_videos": _TypedMediaRestoreSpec(
        basis_field=None,
        basis_kind="artifact-components/video",
        artifact_key=ArtifactKey.episode_video,
        visual_kind="artifact-visual/video-reference",
    ),
}


def is_typed_media_restore_resource(resource_type: str) -> bool:
    return resource_type in _TYPED_MEDIA_RESTORE_SPECS


def is_typed_media_version_restorable(resource_type: str, record: Mapping[str, Any]) -> bool:
    """Whether one API version record carries a complete verified restore target."""

    if not is_typed_media_restore_resource(resource_type):
        return True
    try:
        _target_from_record(resource_type, record)
    except (TypeError, ValueError):
        return False
    return True


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

    if not is_typed_media_restore_resource(resource_type):
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
        if current_binding is None and not (project.get("episodes") or []):
            return target.script_file
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
            key = _TYPED_MEDIA_RESTORE_SPECS[resource_type].artifact_key(target.episode, resource_id)
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
    spec = _TYPED_MEDIA_RESTORE_SPECS[resource_type]
    script_file = record.get("execution_script_file")
    if not isinstance(script_file, str) or not script_file.strip():
        raise ValueError("version does not contain complete typed artifact metadata")
    if resource_type == "audio":
        episode = record.get("artifact_episode")
        if type(episode) is not int or episode < 1 or spec.basis_field is None:
            raise ValueError("version does not contain complete typed artifact metadata")
        try:
            basis = ArtifactBasisDescriptor.from_dict(record.get(spec.basis_field))
        except (TypeError, ValueError) as exc:
            raise ValueError("version does not contain complete typed artifact metadata") from exc
        if basis.kind != spec.basis_kind:
            raise ValueError("version does not contain complete typed artifact metadata")
        _validate_audio_basis(record, basis)
    else:
        facts = _validate_video_basis(record, spec)
        episode = facts.episode
        basis = facts.video_descriptor
    created_at = record.get("created_at")
    return TypedMediaRestoreTarget(
        episode=episode,
        script_file=script_file,
        basis=basis,
        created_at=created_at if isinstance(created_at, str) else None,
    )


def _validate_audio_basis(record: Mapping[str, Any], basis: ArtifactBasisDescriptor) -> None:
    text = record.get("prompt")
    provider_id = record.get("tts_provider_id")
    model_id = record.get("tts_model_id")
    voice = record.get("tts_voice")
    speed = record.get("tts_speed")
    duration = record.get("tts_actual_duration_seconds")
    if (
        not isinstance(text, str)
        or not text
        or not isinstance(provider_id, str)
        or not provider_id.strip()
        or not isinstance(model_id, str)
        or not model_id.strip()
        or not isinstance(voice, str)
        or not voice.strip()
        or (
            speed is not None
            and (
                isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or speed <= 0
            )
        )
        or isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
    ):
        raise ValueError("version does not contain complete typed artifact metadata")
    settings = TtsSynthesisSettings(
        provider_id=provider_id,
        model_id=model_id,
        voice=voice,
        speed=speed,
    )
    expected = ArtifactBasisDescriptor.from_basis(build_narration_audio_basis_from_canonical_text(text, settings))
    if basis != expected or record.get("tts_basis_digest") != expected.digest:
        raise ValueError("version does not contain complete typed artifact metadata")


def _validate_video_basis(
    record: Mapping[str, Any],
    spec: _TypedMediaRestoreSpec,
) -> VideoArtifactCurrencyFacts:
    if record.get(VIDEO_ARTIFACT_RESTORE_BLOCKER_FIELD) is not None:
        raise ValueError("version failed paid video output validation")
    schema_version = record.get("execution_checkpoint_schema_version")
    duration_seconds = record.get("execution_duration_seconds")
    request_digest = record.get("execution_request_digest")
    if (
        type(schema_version) is not int
        or schema_version != 3
        or type(duration_seconds) is not int
        or not isinstance(request_digest, str)
        or len(request_digest) != 64
    ):
        raise ValueError("version does not contain complete typed artifact metadata")
    try:
        facts = VideoArtifactCurrencyFacts.from_dict(record.get("artifact_video_currency"))
    except (TypeError, ValueError) as exc:
        raise ValueError("version does not contain complete typed artifact metadata") from exc
    if facts.visual_basis.kind != spec.visual_kind or facts.request_duration_seconds != duration_seconds:
        raise ValueError("version does not contain complete typed artifact metadata")
    return facts


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
    "is_typed_media_restore_resource",
    "is_typed_media_version_restorable",
    "restore_typed_media_version",
]
