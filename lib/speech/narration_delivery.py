"""Narration-audio basis and TTS artifact registration.

This module owns the transport-neutral contract between one unit's narrator text,
the project's TTS settings, and that unit's formal narration-audio artifact.  Video
generation does not read it: narration delivery never affects a video request.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Protocol

from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
    ArtifactKey,
    ArtifactManifest,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.project.resource_paths import resource_relative_path
from lib.speech.narration_config import POST_PRODUCTION, USE_TTS, NarrationDelivery, TtsSynthesisSettings
from lib.speech.speech_composition import SpeechMode, SpeechOwner, SpeechPreparation


class TtsSettingsResolver(Protocol):
    """Narrow configuration seam that answers one project's current TTS settings."""

    async def resolve_tts_synthesis_settings(self, project: dict) -> TtsSynthesisSettings:
        raise NotImplementedError


def canonical_narration_text(preparation: SpeechPreparation) -> str:
    """Return the exact canonical narrator text consumed by synthesis and its basis."""

    parts: list[str] = []
    for utterance in preparation.utterances:
        if utterance.owner is not SpeechOwner.NARRATOR:
            continue
        normalized = unicodedata.normalize("NFC", utterance.text.replace("\r\n", "\n").replace("\r", "\n"))
        stripped = normalized.strip()
        if stripped:
            parts.append(stripped)
    return "\n".join(parts)


def build_narration_audio_basis(
    preparation: SpeechPreparation,
    settings: TtsSynthesisSettings,
) -> ArtifactBasis:
    """Build the minimum formal basis for one unit's paid TTS artifact."""

    if preparation.problems:
        raise ValueError("cannot build narration audio basis from blocked speech preparation")
    if preparation.mode is not SpeechMode.NARRATOR_VOICEOVER:
        raise ValueError("narration audio basis requires narrator voiceover")
    text = canonical_narration_text(preparation)
    if not text:
        raise ValueError("narration audio basis requires non-empty narrator text")
    return build_narration_audio_basis_from_canonical_text(text, settings)


def build_narration_audio_basis_from_canonical_text(
    text: str,
    settings: TtsSynthesisSettings,
) -> ArtifactBasis:
    """Build a TTS basis from already-canonical synthesis facts.

    Version restore uses this seam to verify persisted execution facts without
    reconstructing a synthetic script unit or duplicating the basis schema.
    """

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


async def resolve_tts_synthesis_settings(
    project: dict,
    resolver: TtsSettingsResolver,
) -> TtsSynthesisSettings:
    """Resolve the effective provider/model, voice, and speed for a paid TTS call."""

    return await resolver.resolve_tts_synthesis_settings(project)


def register_narration_audio_transactionally(
    *,
    project_path: Path,
    episode: int,
    preparation: SpeechPreparation,
    settings: TtsSynthesisSettings,
) -> ArtifactBasis:
    """Register a TTS basis while preserving the prior entry on failure."""

    artifact_path = resource_relative_path("audio", preparation.unit_id)
    key = ArtifactKey.episode_audio(episode, preparation.unit_id)
    basis = build_narration_audio_basis(preparation, settings)
    adapter = ProjectArtifactManifestAdapter(project_path)
    previous = adapter.get_entry(key)
    expected = ArtifactManifestEntry(artifact_path=artifact_path, basis_digest=basis.digest)
    try:
        ArtifactManifest(adapter).register(key, artifact_path=artifact_path, basis=basis)
    except BaseException:
        try:
            current = adapter.get_entry(key)
            if current == expected:
                if previous is None:
                    adapter.delete_entry(key)
                else:
                    adapter.put_entry(key, previous)
        except BaseException as rollback_error:
            raise RuntimeError("TTS basis registration failed and rollback was incomplete") from rollback_error
        raise
    return basis


__all__ = [
    "POST_PRODUCTION",
    "USE_TTS",
    "NarrationDelivery",
    "TtsSettingsResolver",
    "TtsSynthesisSettings",
    "build_narration_audio_basis",
    "build_narration_audio_basis_from_canonical_text",
    "canonical_narration_text",
    "register_narration_audio_transactionally",
    "resolve_tts_synthesis_settings",
]
