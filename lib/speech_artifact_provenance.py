"""Canonical sound-owned provenance for video, subtitles, and presentations.

The builders describe artifact currency only. They do not persist a narration
delivery choice, generate subtitle timing, mix audio, submit provider work, or
own a second stale store beside :mod:`lib.artifact_manifest`.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from lib.artifact_manifest import ArtifactBasis, ArtifactBasisDescriptor
from lib.asset_types import asset_name_comparison_key, normalize_asset_bucket
from lib.narration_delivery import POST_PRODUCTION, USE_TTS
from lib.speech_composition import SpeechMode, SpeechOwner, SpeechPreparation

RenditionVariant = Literal["post_production", "use_tts"]
_DIGEST_RE = re.compile(r"sha256-v1:[0-9a-f]{64}\Z")
_DEFAULT_SUBTITLE_TIMING_POLICY: Mapping[str, object] = {
    "kind": "mechanical-text-length",
    "version": 1,
}
_DEFAULT_PRESENTATION_MIX_POLICY: Mapping[str, object] = {
    "kind": "provider-original-plus-optional-tts",
    "version": 1,
    "provider_video_gain": 1.0,
    "narration_audio_gain": 1.0,
}


@dataclass(frozen=True, slots=True)
class CharacterVoiceEvidence:
    """Effective voice facts for one speaking character in a paid video request."""

    speaker: str
    voice_style: str = ""
    reference_audio: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.speaker, str) or not self.speaker.strip():
            raise ValueError("speaker must be a non-empty string")
        if not isinstance(self.voice_style, str):
            raise ValueError("voice_style must be a string")
        if self.reference_audio is not None and not isinstance(self.reference_audio, Path):
            raise TypeError("reference_audio must be a Path or null")
        object.__setattr__(self, "speaker", asset_name_comparison_key(self.speaker))
        object.__setattr__(self, "voice_style", _canonical_text(self.voice_style))

    def basis_input(self) -> dict[str, object]:
        return {
            "speaker": self.speaker,
            "voice_style": self.voice_style,
            "reference_audio_digest": (
                media_content_digest(self.reference_audio) if self.reference_audio is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class SelectedMediaEvidence:
    """Identity and observed boundary of one selected formal upstream medium."""

    basis: ArtifactBasisDescriptor
    content_digest: str
    actual_duration_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.basis, ArtifactBasisDescriptor):
            raise TypeError("basis must be an ArtifactBasisDescriptor")
        if not isinstance(self.content_digest, str) or _DIGEST_RE.fullmatch(self.content_digest) is None:
            raise ValueError("content_digest must be a canonical sha256-v1 digest")
        duration = self.actual_duration_seconds
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration):
            raise ValueError("actual_duration_seconds must be positive and finite")
        if duration <= 0:
            raise ValueError("actual_duration_seconds must be positive and finite")
        object.__setattr__(self, "actual_duration_seconds", float(duration))

    @classmethod
    def from_file(
        cls,
        *,
        basis: ArtifactBasis | ArtifactBasisDescriptor,
        path: Path,
        actual_duration_seconds: float,
    ) -> SelectedMediaEvidence:
        return cls(
            basis=_basis_descriptor("basis", basis),
            content_digest=media_content_digest(path),
            actual_duration_seconds=actual_duration_seconds,
        )

    def basis_input(self) -> dict[str, object]:
        return {
            "basis": self.basis.to_dict(),
            "content_digest": self.content_digest,
            "actual_duration_seconds": self.actual_duration_seconds,
        }


@dataclass(frozen=True, slots=True)
class SubtitleUtteranceEvidence:
    """Canonical spoken text shared by subtitle artifacts and timing adapters."""

    owner: SpeechOwner
    text: str
    speaker: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.owner, SpeechOwner):
            raise TypeError("owner must be a SpeechOwner")
        normalized_text = _canonical_text(self.text)
        if not normalized_text:
            raise ValueError("subtitle utterance text must be non-empty")
        normalized_speaker = (
            asset_name_comparison_key(self.speaker or "") if self.owner is SpeechOwner.CHARACTER else None
        )
        if self.owner is SpeechOwner.CHARACTER and not normalized_speaker:
            raise ValueError("character subtitle utterance requires a speaker")
        if self.owner is SpeechOwner.NARRATOR and self.speaker is not None:
            raise ValueError("narrator subtitle utterance cannot have a speaker")
        object.__setattr__(self, "text", normalized_text)
        object.__setattr__(self, "speaker", normalized_speaker)

    def basis_input(self) -> dict[str, object]:
        return {
            "owner": self.owner.value,
            "speaker": self.speaker,
            "text": self.text,
        }


def project_character_voice_evidence(
    preparation: SpeechPreparation,
    *,
    characters: object,
    voice_style_speakers: Sequence[str] = (),
    reference_audio_paths: Mapping[str, Path] | None = None,
) -> tuple[CharacterVoiceEvidence, ...]:
    """Adapt current character assets to an execution-frozen dependency shape.

    ``voice_style_speakers`` and ``reference_audio_paths`` name only facts that
    were actually wired into the paid request. Rebuilding later uses those same
    logical speakers, so a provider/config change cannot retroactively widen or
    shrink the artifact basis.
    """

    _require_prepared_speech(preparation)
    if preparation.mode is not SpeechMode.CHARACTER_SPEECH:
        return ()
    style_names = {asset_name_comparison_key(name) for name in voice_style_speakers}
    audio_paths = {asset_name_comparison_key(name): path for name, path in (reference_audio_paths or {}).items()}
    if any(not isinstance(path, Path) for path in audio_paths.values()):
        raise TypeError("reference_audio_paths values must be Paths")
    character_bucket = normalize_asset_bucket(characters)
    ordered_speakers = list(
        dict.fromkeys(
            asset_name_comparison_key(utterance.speaker or "")
            for utterance in preparation.utterances
            if utterance.owner is SpeechOwner.CHARACTER
        )
    )
    evidence: list[CharacterVoiceEvidence] = []
    for speaker in ordered_speakers:
        if speaker not in style_names and speaker not in audio_paths:
            continue
        character = character_bucket.get(speaker)
        voice_style = ""
        if speaker in style_names and isinstance(character, Mapping):
            raw_style = character.get("voice_style")
            voice_style = raw_style if isinstance(raw_style, str) else ""
        evidence.append(
            CharacterVoiceEvidence(
                speaker=speaker,
                voice_style=voice_style,
                reference_audio=audio_paths.get(speaker),
            )
        )
    return tuple(evidence)


def build_video_speech_basis(
    preparation: SpeechPreparation,
    *,
    voices: Sequence[CharacterVoiceEvidence] = (),
) -> ArtifactBasis:
    """Describe the speech facts carried by a provider video audio track.

    Narrator text belongs to the independent TTS/post-production paths and is
    intentionally omitted. Character speech includes only effective profiles for
    speakers that occur in this unit, keeping unrelated character edits local.
    """

    mode = _require_prepared_speech(preparation)
    if any(not isinstance(voice, CharacterVoiceEvidence) for voice in voices):
        raise TypeError("voices must contain CharacterVoiceEvidence values")
    profiles: dict[str, CharacterVoiceEvidence] = {}
    for voice in voices:
        if voice.speaker in profiles:
            raise ValueError(f"duplicate voice evidence for speaker: {voice.speaker!r}")
        profiles[voice.speaker] = voice

    if mode is not SpeechMode.CHARACTER_SPEECH:
        return ArtifactBasis.build(
            "artifact-speech/video",
            kind_version=1,
            inputs={"mode": mode.value},
        )

    utterances: list[dict[str, str]] = []
    speaker_order: list[str] = []
    for utterance in preparation.utterances:
        if utterance.owner is not SpeechOwner.CHARACTER:
            raise ValueError("character speech preparation contains a non-character utterance")
        speaker = asset_name_comparison_key(utterance.speaker or "")
        text = _canonical_text(utterance.text)
        if not speaker or not text:
            raise ValueError("character speech basis requires non-empty speaker and text")
        utterances.append({"speaker": speaker, "text": text})
        if speaker not in speaker_order:
            speaker_order.append(speaker)

    return ArtifactBasis.build(
        "artifact-speech/video",
        kind_version=1,
        inputs={
            "mode": mode.value,
            "utterances": utterances,
            "voices": [
                profiles[speaker].basis_input()
                if speaker in profiles
                else {
                    "speaker": speaker,
                    "voice_style": "",
                    "reference_audio_digest": None,
                }
                for speaker in speaker_order
            ],
        },
    )


def build_video_duration_basis(request_duration_seconds: int) -> ArtifactBasis:
    """Describe the paid video request tier, not an observed TTS duration."""

    if type(request_duration_seconds) is not int or request_duration_seconds <= 0:
        raise ValueError("request_duration_seconds must be a positive integer")
    return ArtifactBasis.build(
        "artifact-speech/video-duration",
        kind_version=1,
        inputs={"request_duration_seconds": request_duration_seconds},
    )


def build_mechanical_subtitle_basis(
    preparation: SpeechPreparation,
    *,
    variant: RenditionVariant,
    video: SelectedMediaEvidence,
    narration_audio: SelectedMediaEvidence | None = None,
    timing_policy: Mapping[str, object] = _DEFAULT_SUBTITLE_TIMING_POLICY,
) -> ArtifactBasis:
    """Describe a mechanical subtitle draft without materializing its timeline."""

    mode = _require_prepared_speech(preparation)
    normalized_variant = _variant(variant)
    if not isinstance(video, SelectedMediaEvidence):
        raise TypeError("video must be SelectedMediaEvidence")
    if narration_audio is not None and not isinstance(narration_audio, SelectedMediaEvidence):
        raise TypeError("narration_audio must be SelectedMediaEvidence or null")
    if not isinstance(timing_policy, Mapping):
        raise TypeError("timing_policy must be a mapping")

    narrator_uses_tts = mode is SpeechMode.NARRATOR_VOICEOVER and normalized_variant == USE_TTS
    if narrator_uses_tts and narration_audio is None:
        raise ValueError("use_tts narrator subtitle basis requires narration audio")
    boundary = narration_audio if narrator_uses_tts else video
    assert boundary is not None

    return ArtifactBasis.build(
        "artifact-speech/mechanical-subtitle",
        kind_version=1,
        inputs={
            "variant": normalized_variant,
            "mode": mode.value,
            "utterances": [utterance.basis_input() for utterance in project_subtitle_utterances(preparation)],
            "boundary_media": boundary.basis_input(),
            "timing_policy": dict(timing_policy),
        },
    )


def build_presentation_basis(
    *,
    variant: RenditionVariant,
    video: SelectedMediaEvidence,
    subtitle: ArtifactBasis | ArtifactBasisDescriptor,
    narration_audio: SelectedMediaEvidence | None = None,
    provider_audio_enabled: bool = True,
    mix_policy: Mapping[str, object] = _DEFAULT_PRESENTATION_MIX_POLICY,
) -> ArtifactBasis:
    """Describe a final-presentation variant without performing media mixing."""

    normalized_variant = _variant(variant)
    if not isinstance(video, SelectedMediaEvidence):
        raise TypeError("video must be SelectedMediaEvidence")
    subtitle_descriptor = _basis_descriptor("subtitle", subtitle)
    if narration_audio is not None and not isinstance(narration_audio, SelectedMediaEvidence):
        raise TypeError("narration_audio must be SelectedMediaEvidence or null")
    if not isinstance(provider_audio_enabled, bool):
        raise TypeError("provider_audio_enabled must be a boolean")
    if not isinstance(mix_policy, Mapping):
        raise TypeError("mix_policy must be a mapping")
    if normalized_variant == USE_TTS and narration_audio is None:
        raise ValueError("use_tts presentation basis requires narration audio")
    if normalized_variant == POST_PRODUCTION and narration_audio is not None:
        raise ValueError("post_production presentation basis cannot include narration audio")

    return ArtifactBasis.build(
        "artifact-speech/presentation",
        kind_version=1,
        inputs={
            "variant": normalized_variant,
            "video": video.basis_input(),
            "subtitle": subtitle_descriptor.to_dict(),
            "narration_audio": narration_audio.basis_input() if narration_audio is not None else None,
            "mix_policy": {
                **dict(mix_policy),
                "provider_audio_enabled": provider_audio_enabled,
            },
        },
    )


def project_subtitle_utterances(preparation: SpeechPreparation) -> tuple[SubtitleUtteranceEvidence, ...]:
    """Return the one canonical utterance projection used by basis and timing."""

    _require_prepared_speech(preparation)
    values: list[SubtitleUtteranceEvidence] = []
    for utterance in preparation.utterances:
        text = _canonical_text(utterance.text)
        if not text:
            continue
        values.append(
            SubtitleUtteranceEvidence(
                owner=utterance.owner,
                speaker=utterance.speaker,
                text=text,
            )
        )
    return tuple(values)


def _require_prepared_speech(preparation: SpeechPreparation) -> SpeechMode:
    if not isinstance(preparation, SpeechPreparation):
        raise TypeError("preparation must be a SpeechPreparation")
    if preparation.problems or preparation.mode is None:
        raise ValueError("cannot build artifact basis from blocked speech preparation")
    return preparation.mode


def _basis_descriptor(field: str, value: ArtifactBasis | ArtifactBasisDescriptor) -> ArtifactBasisDescriptor:
    if isinstance(value, ArtifactBasis):
        return ArtifactBasisDescriptor.from_basis(value)
    if isinstance(value, ArtifactBasisDescriptor):
        return value
    raise TypeError(f"{field} must be an ArtifactBasis or ArtifactBasisDescriptor")


def _variant(value: object) -> RenditionVariant:
    if value not in (POST_PRODUCTION, USE_TTS):
        raise ValueError(f"unsupported rendition variant: {value!r}")
    return value  # type: ignore[return-value]


def _canonical_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("speech text must be a string")
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()


def media_content_digest(path: Path) -> str:
    """Return the canonical content identity used by presentation media."""

    if not path.is_file():
        raise FileNotFoundError(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256-v1:{digest.hexdigest()}"


__all__ = [
    "CharacterVoiceEvidence",
    "RenditionVariant",
    "SelectedMediaEvidence",
    "SubtitleUtteranceEvidence",
    "build_mechanical_subtitle_basis",
    "build_presentation_basis",
    "build_video_duration_basis",
    "build_video_speech_basis",
    "media_content_digest",
    "project_subtitle_utterances",
    "project_character_voice_evidence",
]
