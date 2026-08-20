"""Verified provenance carried by selected formal media versions.

This module is deliberately configuration-free.  Migration and version restore
both consume the immutable facts recorded when paid media was produced; neither
may reconstruct those facts from the provider configuration that happens to be
current later.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lib.artifact_manifest import ArtifactBasis, ArtifactBasisDescriptor
from lib.asset_types import ASSET_SPECS, normalize_asset_name
from lib.narration_delivery import TtsSynthesisSettings, build_narration_audio_basis_from_canonical_text
from lib.video_artifact_facts import VIDEO_ARTIFACT_RESTORE_BLOCKER_FIELD, VideoArtifactCurrencyFacts


@dataclass(frozen=True, slots=True)
class TypedMediaVersionTarget:
    """Complete logical identity and frozen basis of one formal media version."""

    episode: int
    script_file: str
    basis: ArtifactBasisDescriptor
    created_at: str | None


_VIDEO_VISUAL_KINDS = {
    "videos": "artifact-visual/video-storyboard",
    "reference_videos": "artifact-visual/video-reference",
}

IMAGE_ARTIFACT_BASIS_FIELD = "artifact_image_basis"
_IMAGE_ASSET_TYPES = {spec.bucket_key: asset_type for asset_type, spec in ASSET_SPECS.items()}
_IMAGE_VISUAL_KINDS: dict[str, frozenset[str]] = {
    **{resource_type: frozenset({"artifact-visual/asset-sheet"}) for resource_type in _IMAGE_ASSET_TYPES},
    "storyboards": frozenset(
        {
            "artifact-visual/storyboard-image",
            "artifact-visual/grid-member",
            "artifact-visual/stale-grid-member",
        }
    ),
    "grids": frozenset({"artifact-visual/grid-composite"}),
}


def is_typed_media_resource(resource_type: str) -> bool:
    return resource_type == "audio" or resource_type in _VIDEO_VISUAL_KINDS


def parse_typed_media_version_target(
    resource_type: str,
    record: Mapping[str, Any],
) -> TypedMediaVersionTarget:
    """Validate a version record without consulting mutable runtime config."""

    if not is_typed_media_resource(resource_type):
        raise ValueError(f"resource type does not carry typed artifact metadata: {resource_type}")
    script_file = record.get("execution_script_file")
    if not isinstance(script_file, str) or not script_file.strip():
        raise ValueError("version does not contain complete typed artifact metadata")

    if resource_type == "audio":
        episode = record.get("artifact_episode")
        if type(episode) is not int or episode < 1:
            raise ValueError("version does not contain complete typed artifact metadata")
        basis = _validated_audio_basis(record)
    else:
        facts = _validated_video_facts(record, visual_kind=_VIDEO_VISUAL_KINDS[resource_type])
        episode = facts.episode
        basis = facts.video_descriptor

    created_at = record.get("created_at")
    return TypedMediaVersionTarget(
        episode=episode,
        script_file=script_file,
        basis=basis,
        created_at=created_at if isinstance(created_at, str) else None,
    )


def parse_typed_audio_settings(record: Mapping[str, Any]) -> TtsSynthesisSettings:
    """Return execution-frozen settings after validating complete audio facts."""

    parse_typed_media_version_target("audio", record)
    return _audio_settings(record)


def parse_image_version_basis(
    resource_type: str,
    resource_id: str,
    record: Mapping[str, Any],
) -> ArtifactBasis:
    """Validate complete generation-frozen evidence carried by an image version."""

    allowed_kinds = _IMAGE_VISUAL_KINDS.get(resource_type)
    if allowed_kinds is None:
        raise ValueError(f"resource type does not carry image artifact metadata: {resource_type}")
    raw = record.get(IMAGE_ARTIFACT_BASIS_FIELD)
    try:
        basis = ArtifactBasis.from_evidence_dict(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("version does not contain complete image artifact metadata") from exc
    if basis.kind not in allowed_kinds or basis.kind_version != 1 or not isinstance(raw, Mapping):
        raise ValueError("version does not contain complete image artifact metadata")
    inputs = raw.get("inputs")
    if not isinstance(inputs, Mapping) or not _image_basis_matches_resource(
        resource_type,
        resource_id,
        basis_kind=basis.kind,
        inputs=inputs,
    ):
        raise ValueError("image artifact metadata does not match the selected resource")
    return basis


def _image_basis_matches_resource(
    resource_type: str,
    resource_id: str,
    *,
    basis_kind: str,
    inputs: Mapping[str, object],
) -> bool:
    asset_type = _IMAGE_ASSET_TYPES.get(resource_type)
    if asset_type is not None:
        asset = inputs.get("asset")
        if not isinstance(asset, Mapping) or asset.get("type") != asset_type:
            return False
        asset_id = asset.get("id")
        return isinstance(asset_id, str) and normalize_asset_name(asset_id) == normalize_asset_name(resource_id)
    if resource_type == "grids":
        return inputs.get("group_id") == resource_id
    if basis_kind == "artifact-visual/storyboard-image":
        return inputs.get("resource_id") == resource_id
    if basis_kind == "artifact-visual/stale-grid-member":
        return inputs.get("resource_id") == resource_id
    cell = inputs.get("cell")
    return isinstance(cell, Mapping) and cell.get("resource_id") == resource_id


def _validated_audio_basis(record: Mapping[str, Any]) -> ArtifactBasisDescriptor:
    try:
        basis = ArtifactBasisDescriptor.from_dict(record.get("artifact_audio_basis"))
    except (TypeError, ValueError) as exc:
        raise ValueError("version does not contain complete typed artifact metadata") from exc
    if basis.kind != "narration-delivery/tts-audio":
        raise ValueError("version does not contain complete typed artifact metadata")

    text = record.get("prompt")
    duration = record.get("tts_actual_duration_seconds")
    if (
        not isinstance(text, str)
        or not text
        or isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
    ):
        raise ValueError("version does not contain complete typed artifact metadata")
    settings = _audio_settings(record)
    expected = ArtifactBasisDescriptor.from_basis(build_narration_audio_basis_from_canonical_text(text, settings))
    if basis != expected or record.get("tts_basis_digest") != expected.digest:
        raise ValueError("version does not contain complete typed artifact metadata")
    return basis


def _audio_settings(record: Mapping[str, Any]) -> TtsSynthesisSettings:
    provider_id = record.get("tts_provider_id")
    model_id = record.get("tts_model_id")
    voice = record.get("tts_voice")
    speed = record.get("tts_speed")
    if (
        not isinstance(provider_id, str)
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
    ):
        raise ValueError("version does not contain complete typed artifact metadata")
    return TtsSynthesisSettings(
        provider_id=provider_id,
        model_id=model_id,
        voice=voice,
        speed=speed,
    )


def _validated_video_facts(
    record: Mapping[str, Any],
    *,
    visual_kind: str,
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
    if facts.visual_basis.kind != visual_kind or facts.request_duration_seconds != duration_seconds:
        raise ValueError("version does not contain complete typed artifact metadata")
    return facts


__all__ = [
    "IMAGE_ARTIFACT_BASIS_FIELD",
    "TypedMediaVersionTarget",
    "is_typed_media_resource",
    "parse_image_version_basis",
    "parse_typed_audio_settings",
    "parse_typed_media_version_target",
]
