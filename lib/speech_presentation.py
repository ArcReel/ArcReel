"""Transport-neutral presentation of one paid video generation unit.

The module is the presentation phase adjacent to :mod:`lib.speech_composition`.
It owns subtitle timing and audio placement, while project/version selection,
media probing, browser playback, and editor serialization remain adapters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Protocol

from lib.artifact_manifest import ArtifactBasis, ArtifactBasisDescriptor
from lib.narration_delivery import POST_PRODUCTION, USE_TTS
from lib.speech_artifact_provenance import (
    RenditionVariant,
    SelectedMediaEvidence,
    SubtitleUtteranceEvidence,
    build_mechanical_subtitle_basis,
    build_presentation_basis,
    project_subtitle_utterances,
)
from lib.speech_composition import SpeechMode, SpeechPreparation

MICROSECONDS_PER_SECOND = 1_000_000
MediaSelection = Literal["current", "history"]
MediaCurrency = Literal["current", "stale"]


class PresentationBoundaryError(ValueError):
    """A requested narration track cannot fit inside its video unit."""


@dataclass(frozen=True, slots=True)
class PresentationMedia:
    """One immutable selected version used by a presentation."""

    artifact_path: str
    version: int
    selection: MediaSelection
    currency: MediaCurrency
    evidence: SelectedMediaEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_path, str) or not self.artifact_path:
            raise ValueError("artifact_path must be a non-empty string")
        if type(self.version) is not int or self.version <= 0:
            raise ValueError("version must be a positive integer")
        if self.selection not in {"current", "history"}:
            raise ValueError("selection must be current or history")
        if self.currency not in {"current", "stale"}:
            raise ValueError("currency must be current or stale")
        if not isinstance(self.evidence, SelectedMediaEvidence):
            raise TypeError("evidence must be SelectedMediaEvidence")


@dataclass(frozen=True, slots=True)
class SubtitleCue:
    """One contiguous, mechanically allocated subtitle interval."""

    start_microseconds: int
    duration_microseconds: int
    text: str
    owner: str
    speaker: str | None

    @property
    def end_microseconds(self) -> int:
        return self.start_microseconds + self.duration_microseconds

    def to_dict(self) -> dict[str, object]:
        return {
            "start_microseconds": self.start_microseconds,
            "duration_microseconds": self.duration_microseconds,
            "text": self.text,
            "owner": self.owner,
            "speaker": self.speaker,
        }


class SubtitleTimingAdapter(Protocol):
    """Replaceable timing policy that does not own speech or artifact identity."""

    @property
    def basis_identity(self) -> dict[str, object]:
        raise NotImplementedError

    def distribute(
        self,
        utterances: tuple[SubtitleUtteranceEvidence, ...],
        *,
        boundary_microseconds: int,
    ) -> tuple[SubtitleCue, ...]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class MechanicalSubtitleTiming:
    """Allocate a real media boundary by normalized Unicode text length."""

    policy_version: int = 1

    def __post_init__(self) -> None:
        if type(self.policy_version) is not int or self.policy_version <= 0:
            raise ValueError("policy_version must be a positive integer")

    @property
    def basis_identity(self) -> dict[str, object]:
        return {
            "kind": "mechanical-text-length",
            "version": self.policy_version,
        }

    def distribute(
        self,
        utterances: tuple[SubtitleUtteranceEvidence, ...],
        *,
        boundary_microseconds: int,
    ) -> tuple[SubtitleCue, ...]:
        if type(boundary_microseconds) is not int or boundary_microseconds <= 0:
            raise ValueError("boundary_microseconds must be a positive integer")
        if not utterances:
            return ()
        weights = tuple(len(utterance.text) for utterance in utterances)
        total_weight = sum(weights)
        if total_weight <= 0:  # pragma: no cover - SubtitleUtteranceEvidence invariant
            raise ValueError("subtitle utterances must contain visible text")

        cues: list[SubtitleCue] = []
        cumulative = 0
        for utterance, weight in zip(utterances, weights, strict=True):
            start = boundary_microseconds * cumulative // total_weight
            cumulative += weight
            end = boundary_microseconds * cumulative // total_weight
            if end <= start:
                raise ValueError("media boundary is too short for its subtitle utterances")
            cues.append(
                SubtitleCue(
                    start_microseconds=start,
                    duration_microseconds=end - start,
                    text=utterance.text,
                    owner=utterance.owner.value,
                    speaker=utterance.speaker,
                )
            )
        return tuple(cues)


@dataclass(frozen=True, slots=True)
class VideoPresentationTrack:
    media: PresentationMedia
    start_microseconds: int
    duration_microseconds: int
    audio_enabled: bool
    gain: float

    def to_dict(self) -> dict[str, object]:
        return {
            **_media_dict(self.media),
            "start_microseconds": self.start_microseconds,
            "duration_microseconds": self.duration_microseconds,
            "audio_enabled": self.audio_enabled,
            "gain": self.gain,
        }


@dataclass(frozen=True, slots=True)
class NarrationPresentationTrack:
    media: PresentationMedia
    start_microseconds: int
    duration_microseconds: int
    gain: float

    def to_dict(self) -> dict[str, object]:
        return {
            **_media_dict(self.media),
            "start_microseconds": self.start_microseconds,
            "duration_microseconds": self.duration_microseconds,
            "gain": self.gain,
        }


@dataclass(frozen=True, slots=True)
class SpeechPresentation:
    """Single source consumed by browser, download, and editing adapters."""

    unit_id: str
    variant: RenditionVariant
    speech_mode: SpeechMode
    selection: MediaSelection
    currency: MediaCurrency
    video: VideoPresentationTrack
    narration_audio: NarrationPresentationTrack | None
    subtitles: tuple[SubtitleCue, ...]
    subtitle_basis: ArtifactBasis
    presentation_basis: ArtifactBasis
    timing: Literal["mechanical"] = "mechanical"
    subtitles_adjustable: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "unit_id": self.unit_id,
            "variant": self.variant,
            "speech_mode": self.speech_mode.value,
            "selection": self.selection,
            "currency": self.currency,
            "video": self.video.to_dict(),
            "narration_audio": self.narration_audio.to_dict() if self.narration_audio is not None else None,
            "subtitles": [cue.to_dict() for cue in self.subtitles],
            "subtitle_basis": ArtifactBasisDescriptor.from_basis(self.subtitle_basis).to_dict(),
            "presentation_basis": ArtifactBasisDescriptor.from_basis(self.presentation_basis).to_dict(),
            "timing": self.timing,
            "subtitles_adjustable": self.subtitles_adjustable,
        }


def materialize_speech_presentation(
    preparation: SpeechPreparation,
    *,
    variant: RenditionVariant,
    video: PresentationMedia,
    provider_audio_enabled: bool,
    narration_audio: PresentationMedia | None = None,
    timing: SubtitleTimingAdapter | None = None,
) -> SpeechPresentation:
    """Materialize one validated presentation from selected real media."""

    if not isinstance(preparation, SpeechPreparation) or preparation.problems or preparation.mode is None:
        raise ValueError("presentation requires an admitted speech preparation")
    if variant not in {POST_PRODUCTION, USE_TTS}:
        raise ValueError(f"unsupported rendition variant: {variant!r}")
    if not isinstance(video, PresentationMedia):
        raise TypeError("video must be PresentationMedia")
    if not isinstance(provider_audio_enabled, bool):
        raise TypeError("provider_audio_enabled must be a boolean")
    if narration_audio is not None and not isinstance(narration_audio, PresentationMedia):
        raise TypeError("narration_audio must be PresentationMedia or null")
    if variant == USE_TTS and preparation.mode is not SpeechMode.NARRATOR_VOICEOVER:
        raise ValueError("use_tts presentation requires narrator voiceover")
    if variant == USE_TTS and narration_audio is None:
        raise ValueError("use_tts presentation requires narration audio")
    if variant == POST_PRODUCTION and narration_audio is not None:
        raise ValueError("post_production presentation cannot include narration audio")

    video_duration = _duration_microseconds(video.evidence.actual_duration_seconds)
    narration_duration: int | None = None
    if narration_audio is not None:
        narration_duration = _duration_microseconds(narration_audio.evidence.actual_duration_seconds)
        if narration_duration > video_duration:
            raise PresentationBoundaryError(
                f"narration audio exceeds video boundary: {narration_duration} > {video_duration} microseconds"
            )

    timing_adapter = timing or MechanicalSubtitleTiming()
    utterances = project_subtitle_utterances(preparation)
    subtitle_boundary = narration_duration if narration_duration is not None else video_duration
    assert subtitle_boundary is not None
    subtitles = timing_adapter.distribute(utterances, boundary_microseconds=subtitle_boundary)
    subtitle_basis = build_mechanical_subtitle_basis(
        preparation,
        variant=variant,
        video=video.evidence,
        narration_audio=narration_audio.evidence if narration_audio is not None else None,
        timing_policy=timing_adapter.basis_identity,
    )
    presentation_basis = build_presentation_basis(
        variant=variant,
        video=video.evidence,
        subtitle=subtitle_basis,
        narration_audio=narration_audio.evidence if narration_audio is not None else None,
        provider_audio_enabled=provider_audio_enabled,
    )
    sources = (video,) if narration_audio is None else (video, narration_audio)
    selection: MediaSelection = "history" if any(source.selection == "history" for source in sources) else "current"
    currency: MediaCurrency = "stale" if any(source.currency == "stale" for source in sources) else "current"
    return SpeechPresentation(
        unit_id=preparation.unit_id,
        variant=variant,
        speech_mode=preparation.mode,
        selection=selection,
        currency=currency,
        video=VideoPresentationTrack(
            media=video,
            start_microseconds=0,
            duration_microseconds=video_duration,
            audio_enabled=provider_audio_enabled,
            gain=1.0 if provider_audio_enabled else 0.0,
        ),
        narration_audio=(
            NarrationPresentationTrack(
                media=narration_audio,
                start_microseconds=0,
                duration_microseconds=narration_duration,
                gain=1.0,
            )
            if narration_audio is not None and narration_duration is not None
            else None
        ),
        subtitles=subtitles,
        subtitle_basis=subtitle_basis,
        presentation_basis=presentation_basis,
    )


def _duration_microseconds(seconds: float) -> int:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("media duration must be positive and finite")
    duration = round(float(seconds) * MICROSECONDS_PER_SECOND)
    if duration <= 0:
        raise ValueError("media duration is below one microsecond")
    return duration


def _media_dict(media: PresentationMedia) -> dict[str, object]:
    return {
        "artifact_path": media.artifact_path,
        "version": media.version,
        "selection": media.selection,
        "currency": media.currency,
        "basis": media.evidence.basis.to_dict(),
        "content_digest": media.evidence.content_digest,
        "actual_duration_seconds": media.evidence.actual_duration_seconds,
    }


__all__ = [
    "MechanicalSubtitleTiming",
    "MediaCurrency",
    "MediaSelection",
    "NarrationPresentationTrack",
    "PresentationBoundaryError",
    "PresentationMedia",
    "SpeechPresentation",
    "SubtitleCue",
    "SubtitleTimingAdapter",
    "VideoPresentationTrack",
    "materialize_speech_presentation",
]
