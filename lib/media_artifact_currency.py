"""Configuration-free current bases for selected typed media artifacts.

The selected version freezes execution-only dependency shape (duration tiers,
reference clamping, voice-style speakers, and TTS settings).  Current currency
reprojects only durable project/script inputs through that frozen shape; it
never consults whichever provider configuration happens to be active later.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from lib.artifact_manifest import (
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.artifact_version_provenance import parse_typed_audio_settings, parse_typed_media_version_target
from lib.asset_types import asset_name_comparison_key
from lib.narration_delivery import TtsSynthesisSettings, build_narration_audio_basis
from lib.project_manager import ProjectManager, resolve_episode_script_binding
from lib.reference_video.duration_slots import resolve_duration_slot
from lib.reference_video.prompt_render import resolve_reference_audio_paths
from lib.reference_video.request_projection import (
    FilesystemReferenceAssets,
    canonicalize_references,
    clamp_reference_assets,
    hydrate_reference_assets,
    resolve_reference_assets,
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
    admission = admit_script_unit(kind, item)
    if not admission.allowed:
        return None

    style_speakers = artifact_currency.voice_style_speakers
    audio_speakers = _execution_reference_audio_speakers(version_metadata.get("execution_provider_media"))
    if audio_speakers is None:
        return None
    available_audio = resolve_reference_audio_paths(project, project_path)
    selected_audio = {speaker: available_audio[speaker] for speaker in audio_speakers if speaker in available_audio}
    speech = build_video_speech_basis(
        admission.preparation,
        voices=project_character_voice_evidence(
            admission.preparation,
            characters=project.get("characters"),
            voice_style_speakers=style_speakers,
            reference_audio_paths=selected_audio,
        ),
    )

    if resource_type == "videos":
        prompt = item.get("video_prompt")
        storyboard, end_frame = resolve_storyboard_video_inputs(
            project_path=project_path,
            resource_id=resource_id,
            item=item,
        )
        visual = build_storyboard_video_artifact_visual_basis(
            resource_id=resource_id,
            visual_prompt=prompt,
            storyboard_image=storyboard,
            end_frame_image=end_frame,
            aspect_ratio=resolve_video_aspect_ratio(project),
        )
    elif resource_type == "reference_videos":
        limit = artifact_currency.reference_image_limit
        declared = canonicalize_references(item.get("references"))
        resolved = resolve_reference_assets(project, project_path, item)
        hydration = hydrate_reference_assets(declared, resolved, FilesystemReferenceAssets(project_path))
        if hydration.missing:
            return None
        visual = build_reference_video_artifact_visual_basis(
            unit=item,
            request_assets=clamp_reference_assets(hydration.available, limit),
            style=project.get("style") if isinstance(project.get("style"), str) else None,
            aspect_ratio=resolve_video_aspect_ratio(project),
        )
    else:
        return None

    duration = _current_duration_tier_basis(
        project_path=project_path,
        project=project,
        item=item,
        resource_id=resource_id,
        episode=episode,
        versions=versions,
        version_metadata=version_metadata,
        artifact_currency=artifact_currency,
        preparation=admission.preparation,
        current_tts_settings=current_tts_settings,
        resolve_audio_manifest_entry=resolve_audio_manifest_entry,
    )
    if duration is None:
        return None
    return ArtifactBasisDescriptor.from_basis(
        compose_video_artifact_basis(visual=visual, speech=speech, duration=duration)
    )


def _current_duration_tier_basis(
    *,
    project_path: Path,
    project: Mapping[str, Any],
    item: Mapping[str, Any],
    resource_id: str,
    episode: int,
    versions: VersionManager,
    version_metadata: Mapping[str, Any],
    artifact_currency: VideoArtifactCurrencyFacts,
    preparation: SpeechPreparation,
    current_tts_settings: TtsSynthesisSettings | None,
    resolve_audio_manifest_entry: AudioManifestEntryResolver | None,
):
    tiers = artifact_currency.duration_tiers
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
    "build_current_audio_artifact_basis",
    "build_current_video_artifact_basis",
]
