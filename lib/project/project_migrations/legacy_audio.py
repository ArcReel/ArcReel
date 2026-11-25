from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.artifact_version_provenance import AudioVersionTarget, validate_audio_target
from lib.artifacts.media_artifact_currency import AudioBasisRules
from lib.artifacts.version_manager import VersionManager
from lib.project.project_manager import resolve_episode_script_binding
from lib.project.project_schema import parse_project_schema_version
from lib.project.resource_paths import resource_relative_path
from lib.script.reference_video.duration_slots import resolve_duration_slot
from lib.speech.narration_config import TtsSynthesisSettings, project_tts_settings
from lib.speech.narration_delivery import canonical_narration_text
from lib.speech.speech_artifact_provenance import build_video_duration_basis
from lib.speech.speech_composition import SpeechMode, SpeechPreparation, admit_script_unit


def legacy_audio_basis(text: str, settings: TtsSynthesisSettings) -> ArtifactBasis:
    if not text:
        raise ValueError("narration audio basis requires non-empty canonical text")
    return ArtifactBasis.build(
        "narration-delivery/tts-audio",
        kind_version=1,
        inputs={
            "text": text,
            "provider_id": settings.provider_id,
            "model_id": settings.model_id,
            "voice": settings.voice,
            "speed": settings.speed,
        },
    )


def legacy_prepared_basis(preparation: SpeechPreparation, settings: TtsSynthesisSettings) -> ArtifactBasis:
    if preparation.problems or preparation.mode is not SpeechMode.NARRATOR_VOICEOVER:
        raise ValueError("invalid narration")
    return legacy_audio_basis(canonical_narration_text(preparation), settings)


def read_v1_audio_target(record: Mapping[str, Any]) -> AudioVersionTarget:
    return validate_audio_target({"tts_speed": None, **record}, legacy_audio_basis)


def build_current_audio_artifact_basis(
    *,
    project: Mapping[str, Any],
    item: Mapping[str, Any],
    skeleton_kind: str,
    version_record: Mapping[str, Any],
) -> ArtifactBasisDescriptor | None:
    """Reproject current narration text through the project's current TTS settings.

    A current-schema project without a complete TTS snapshot has no current basis.
    """

    try:
        target = read_v1_audio_target(version_record)
        if parse_project_schema_version(project) >= 16:
            settings = project_tts_settings(project)
            if settings is None:
                return None
        else:
            settings = target.settings
        admission = admit_script_unit(skeleton_kind, item)
        current = ArtifactBasisDescriptor.from_basis(legacy_prepared_basis(admission.preparation, settings))
    except (TypeError, ValueError):
        return None
    if target.basis.kind != current.kind:
        return None
    return current


class _V1AudioBasisRules:
    read = staticmethod(read_v1_audio_target)
    project = staticmethod(build_current_audio_artifact_basis)


V1_AUDIO_RULES: AudioBasisRules = _V1AudioBasisRules()


def legacy_narrated_duration(
    *,
    project_path: Path,
    project: Mapping[str, Any],
    item: Mapping[str, Any],
    skeleton_kind: str,
    resource_id: str,
    episode: int,
    record: Mapping[str, Any],
    tiers: tuple[int, ...],
    audio_entries: Mapping[ArtifactKey, ArtifactManifestEntry] | None,
) -> ArtifactBasis | None:
    """schema < 16 的迁移读法：只有已登记且时新的选中配音会抬升旧视频基准。"""

    if parse_project_schema_version(project) >= 16:
        return None
    narration = record.get("execution_narration")
    if not isinstance(narration, Mapping) or narration.get("delivery") != "use_tts":
        return None
    history = VersionManager(project_path).get_versions("audio", resource_id)
    selected = next((entry for entry in history["versions"] if entry.get("is_current")), None)
    if not isinstance(selected, Mapping):
        return None
    try:
        target = V1_AUDIO_RULES.read(selected)
        current = V1_AUDIO_RULES.project(
            project=project, item=item, skeleton_kind=skeleton_kind, version_record=selected
        )
    except (TypeError, ValueError):
        return None
    actual = selected.get("tts_actual_duration_seconds")
    planned = item.get("duration_seconds")
    if type(planned) is not int or planned <= 0:
        planned = project.get("default_duration")
    path = resource_relative_path("audio", resource_id)
    key = ArtifactKey.episode_audio(episode, resource_id)
    entry = (
        audio_entries.get(key)
        if audio_entries is not None
        else ProjectArtifactManifestAdapter(project_path).get_entry(key)
    )
    if (
        target.episode != episode
        or resolve_episode_script_binding(project, episode, target.script_file) is None
        or current != target.basis
        or entry != ArtifactManifestEntry(path, target.basis.digest)
        or not (project_path / path).is_file()
        or isinstance(actual, bool)
        or not isinstance(actual, int | float)
        or actual <= 0
        or type(planned) is not int
        or planned <= 0
    ):
        return None
    slot = resolve_duration_slot(max(planned, actual), tiers)
    if max(planned, actual) > slot.seconds:
        raise ValueError("legacy narration duration exceeds the paid tiers")
    return build_video_duration_basis(slot.seconds)
