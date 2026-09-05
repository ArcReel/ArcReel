"""Configuration-free current bases for selected typed media artifacts.

The selected version freezes execution-only dependency shape (duration tiers,
reference clamping, voice-style speakers, and TTS settings).  Current currency
reprojects only durable project/script inputs through that frozen shape; it
never consults whichever provider configuration happens to be active later.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.artifact_version_provenance import parse_typed_audio_settings, parse_typed_media_version_target
from lib.asset_types import asset_name_comparison_key
from lib.character_voice import character_voice_binding
from lib.narration_delivery import TtsSynthesisSettings, build_narration_audio_basis
from lib.project_manager import ProjectManager, resolve_episode_script_binding
from lib.reference_video.duration_slots import resolve_duration_slot
from lib.reference_video.prompt_render import resolve_reference_audio_paths
from lib.reference_video.request_projection import (
    FilesystemReferenceAssets,
    clamp_reference_assets,
    hydrate_reference_assets,
    resolve_reference_assets,
    unit_reference_declarations,
)
from lib.resource_paths import resource_relative_path
from lib.script_editor import resolve_items
from lib.speech_artifact_provenance import (
    build_video_duration_basis,
    build_video_speech_basis,
    project_character_voice_evidence,
)
from lib.speech_composition import SpeechPreparation, admit_script_unit
from lib.storyboard_sequence import resolve_storyboard_video_inputs
from lib.version_manager import VersionManager
from lib.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.video_visual_provenance import resolve_video_aspect_ratio
from lib.visual_artifact_provenance import (
    build_reference_video_artifact_visual_basis,
    build_storyboard_video_artifact_visual_basis,
)

AudioManifestEntryResolver = Callable[[ArtifactKey], ArtifactManifestEntry | None]


def build_current_audio_artifact_basis(
    *,
    item: Mapping[str, Any],
    skeleton_kind: str,
    version_record: Mapping[str, Any],
) -> ArtifactBasisDescriptor | None:
    """Reproject current narration text through execution-frozen TTS settings."""

    try:
        target = parse_typed_media_version_target("audio", version_record)
        settings = parse_typed_audio_settings(version_record)
        admission = admit_script_unit(skeleton_kind, item)
        current = ArtifactBasisDescriptor.from_basis(build_narration_audio_basis(admission.preparation, settings))
    except (TypeError, ValueError):
        return None
    if target.basis.kind != current.kind:
        return None
    return current


@dataclass(frozen=True, slots=True)
class VideoExecutionShape:
    """The execution-frozen dependency shape a video's current basis is reprojected through."""

    voice_style_speakers: tuple[str, ...]
    reference_audio_speakers: tuple[str, ...]
    reference_image_limit: int | None
    duration_tiers: tuple[int, ...]

    @classmethod
    def from_version_record(
        cls,
        artifact_currency: VideoArtifactCurrencyFacts,
        version_metadata: Mapping[str, Any],
    ) -> VideoExecutionShape:
        audio_speakers = _execution_reference_audio_speakers(version_metadata.get("execution_provider_media"))
        if audio_speakers is None:
            raise ValueError("version record does not list its provider media")
        return cls(
            voice_style_speakers=artifact_currency.voice_style_speakers,
            reference_audio_speakers=audio_speakers,
            reference_image_limit=artifact_currency.reference_image_limit,
            duration_tiers=artifact_currency.duration_tiers,
        )


@dataclass(frozen=True, slots=True)
class VideoBasisComponents:
    visual: ArtifactBasis
    speech: ArtifactBasis
    duration: ArtifactBasis

    def compose(self) -> ArtifactBasis:
        return compose_video_artifact_basis(visual=self.visual, speech=self.speech, duration=self.duration)


def project_video_basis_components(
    *,
    project_path: Path,
    project: dict[str, Any],
    item: dict[str, Any],
    skeleton_kind: str,
    resource_type: str,
    resource_id: str,
    episode: int,
    shape: VideoExecutionShape,
    versions: VersionManager,
    version_metadata: Mapping[str, Any],
    current_tts_settings: TtsSynthesisSettings | None = None,
    resolve_audio_manifest_entry: AudioManifestEntryResolver | None = None,
) -> VideoBasisComponents:
    """Project the current script item through ``shape`` into the three video basis components.

    The registration path (backfill) and the comparison path (currency) both
    build their basis here, so the two can never disagree on what a video
    depends on. Raises ``ValueError`` naming what could not be projected.
    """

    admission = admit_script_unit(skeleton_kind, item)
    if not admission.allowed:
        raise ValueError("script unit is not admitted for speech composition")

    # 角色声音绑定方式属于交付内容（ADR 0062 的分界线：这一位改变用户拿到的成品）：项目切回提示词
    # 软约束后，已挂过参考音频的成片不再对得上当前设置，投影里因此不再重放那批音频，条目随之判
    # stale。反向切换不触发——那批成片本就没挂过音频，参考音频档下渲染出的画面与声明与它们一致。
    replay_audio = character_voice_binding(project) == "reference_audio"
    available_audio = resolve_reference_audio_paths(project, project_path) if replay_audio else {}
    selected_audio = {
        speaker: available_audio[speaker] for speaker in shape.reference_audio_speakers if speaker in available_audio
    }
    speech = build_video_speech_basis(
        admission.preparation,
        voices=project_character_voice_evidence(
            admission.preparation,
            characters=project.get("characters"),
            voice_style_speakers=shape.voice_style_speakers,
            reference_audio_paths=selected_audio,
        ),
    )

    if resource_type == "videos":
        prompt = item.get("video_prompt")
        try:
            storyboard, end_frame = resolve_storyboard_video_inputs(
                project_path=project_path,
                resource_id=resource_id,
                item=item,
            )
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError(f"storyboard inputs are unavailable: {exc}") from exc
        visual = build_storyboard_video_artifact_visual_basis(
            resource_id=resource_id,
            visual_prompt=prompt,
            storyboard_image=storyboard,
            end_frame_image=end_frame,
            aspect_ratio=resolve_video_aspect_ratio(project),
        )
    elif resource_type == "reference_videos":
        declared = unit_reference_declarations(project, item)
        resolved = resolve_reference_assets(project, project_path, item)
        hydration = hydrate_reference_assets(declared, resolved, FilesystemReferenceAssets(project_path))
        if hydration.missing:
            raise ValueError("declared reference assets are missing")
        visual = build_reference_video_artifact_visual_basis(
            unit=item,
            request_assets=clamp_reference_assets(hydration.available, shape.reference_image_limit),
            style=project.get("style") if isinstance(project.get("style"), str) else None,
            aspect_ratio=resolve_video_aspect_ratio(project),
        )
    else:
        raise ValueError(f"resource type does not carry video artifact metadata: {resource_type}")

    duration = _current_duration_tier_basis(
        project_path=project_path,
        project=project,
        item=item,
        resource_id=resource_id,
        episode=episode,
        versions=versions,
        version_metadata=version_metadata,
        duration_tiers=shape.duration_tiers,
        preparation=admission.preparation,
        current_tts_settings=current_tts_settings,
        resolve_audio_manifest_entry=resolve_audio_manifest_entry,
    )
    if duration is None:
        raise ValueError("planned duration cannot be placed on the paid duration tiers")
    return VideoBasisComponents(visual=visual, speech=speech, duration=duration)


def build_current_video_artifact_basis(
    *,
    project_path: Path,
    project: dict[str, Any],
    script: dict[str, Any],
    resource_type: str,
    resource_id: str,
    versions: VersionManager,
    version_metadata: Mapping[str, Any],
    current_tts_settings: TtsSynthesisSettings | None = None,
    resolve_audio_manifest_entry: AudioManifestEntryResolver | None = None,
) -> ArtifactBasisDescriptor | None:
    """Rebuild current video inputs using only frozen execution dependency shape."""

    try:
        artifact_currency = VideoArtifactCurrencyFacts.from_dict(version_metadata.get("artifact_video_currency"))
    except (TypeError, ValueError):
        return None
    episode = artifact_currency.episode
    script_file = version_metadata.get("execution_script_file")
    if not isinstance(script_file, str) or not script_file:
        return None
    try:
        current_episode = ProjectManager.resolve_episode_from_script(script, script_file)
    except ValueError:
        return None
    if current_episode != episode:
        return None
    if resolve_episode_script_binding(project, episode, script_file) is None:
        return None

    items, id_field, kind = resolve_items(script)
    item = next(
        (
            candidate
            for candidate in items
            if isinstance(candidate, dict) and str(candidate.get(id_field)) == resource_id
        ),
        None,
    )
    if item is None:
        return None
    try:
        shape = VideoExecutionShape.from_version_record(artifact_currency, version_metadata)
        components = project_video_basis_components(
            project_path=project_path,
            project=project,
            item=item,
            skeleton_kind=kind,
            resource_type=resource_type,
            resource_id=resource_id,
            episode=episode,
            shape=shape,
            versions=versions,
            version_metadata=version_metadata,
            current_tts_settings=current_tts_settings,
            resolve_audio_manifest_entry=resolve_audio_manifest_entry,
        )
    except (KeyError, OSError, TypeError, ValueError):
        return None
    return ArtifactBasisDescriptor.from_basis(components.compose())


def _current_duration_tier_basis(
    *,
    project_path: Path,
    project: Mapping[str, Any],
    item: Mapping[str, Any],
    resource_id: str,
    episode: int,
    versions: VersionManager,
    version_metadata: Mapping[str, Any],
    duration_tiers: tuple[int, ...],
    preparation: SpeechPreparation,
    current_tts_settings: TtsSynthesisSettings | None,
    resolve_audio_manifest_entry: AudioManifestEntryResolver | None,
) -> ArtifactBasis | None:
    tiers = duration_tiers
    planned = item.get("duration_seconds")
    if type(planned) is not int or planned <= 0:
        planned = project.get("default_duration")
    if type(planned) is not int or planned <= 0:
        return None
    duration_input: int | float = planned
    narration = version_metadata.get("execution_narration")
    if isinstance(narration, Mapping) and narration.get("delivery") == "use_tts":
        actual = _selected_current_tts_duration(
            project_path=project_path,
            versions=versions,
            episode=episode,
            resource_id=resource_id,
            preparation=preparation,
            current_tts_settings=current_tts_settings,
            resolve_audio_manifest_entry=resolve_audio_manifest_entry,
        )
        if actual is not None:
            duration_input = max(duration_input, actual)
    slot = resolve_duration_slot(duration_input, tiers)
    if slot.adjustment == "down" and duration_input > slot.seconds:
        return None
    return build_video_duration_basis(slot.seconds)


def _selected_current_tts_duration(
    *,
    project_path: Path,
    versions: VersionManager,
    episode: int,
    resource_id: str,
    preparation: SpeechPreparation,
    current_tts_settings: TtsSynthesisSettings | None,
    resolve_audio_manifest_entry: AudioManifestEntryResolver | None,
) -> float | None:
    history = versions.get_versions("audio", resource_id)
    selected = next((record for record in history["versions"] if record.get("is_current")), None)
    if not isinstance(selected, dict):
        return None
    raw_basis = selected.get("artifact_audio_basis")
    actual = selected.get("tts_actual_duration_seconds")
    if not isinstance(raw_basis, Mapping) or isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return None
    try:
        descriptor = ArtifactBasisDescriptor.from_dict(raw_basis)
    except ValueError:
        return None
    if descriptor.kind != "narration-delivery/tts-audio" or actual <= 0:
        return None
    if current_tts_settings is None:
        return None
    try:
        expected = ArtifactBasisDescriptor.from_basis(build_narration_audio_basis(preparation, current_tts_settings))
    except (TypeError, ValueError):
        return None
    if descriptor != expected:
        return None
    key = ArtifactKey.episode_audio(episode, resource_id)
    entry = (
        resolve_audio_manifest_entry(key)
        if resolve_audio_manifest_entry is not None
        else ProjectArtifactManifestAdapter(project_path).get_entry(key)
    )
    expected_path = resource_relative_path("audio", resource_id)
    if entry is None or entry.artifact_path != expected_path or entry.basis_digest != descriptor.digest:
        return None
    if not (project_path / expected_path).is_file():
        return None
    return float(actual)


def _execution_reference_audio_speakers(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        return None
    speakers: list[str] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            return None
        if raw.get("role") != "reference_audio":
            continue
        name = raw.get("logical_name")
        if not isinstance(name, str) or not name:
            return None
        canonical = asset_name_comparison_key(name)
        if canonical not in speakers:
            speakers.append(canonical)
    return tuple(speakers)


__all__ = [
    "AudioManifestEntryResolver",
    "VideoBasisComponents",
    "VideoExecutionShape",
    "build_current_audio_artifact_basis",
    "build_current_video_artifact_basis",
    "project_video_basis_components",
]
