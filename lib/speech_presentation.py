"""Transport-neutral presentation of one paid video generation unit.

The module is the presentation phase adjacent to :mod:`lib.speech_composition`.
It owns subtitle timing and audio placement, while project/version selection,
media probing, browser playback, and editor serialization remain adapters.
"""

from __future__ import annotations

import math
import re
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
from lib.speech_composition import SpeechMode, SpeechOwner, SpeechPreparation

MICROSECONDS_PER_SECOND = 1_000_000
MediaSelection = Literal["current", "history"]
MediaCurrency = Literal["current", "stale"]
PresentationProvenance = Literal["verified", "unavailable"]
_CONTENT_DIGEST_PATTERN = re.compile(r"sha256-v1:[0-9a-f]{64}\Z")


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
    owner: SpeechOwner
    speaker: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.owner, SpeechOwner):
            raise TypeError("owner must be a SpeechOwner")

    @property
    def end_microseconds(self) -> int:
        return self.start_microseconds + self.duration_microseconds

    def to_dict(self) -> dict[str, object]:
        return {
            "start_microseconds": self.start_microseconds,
            "duration_microseconds": self.duration_microseconds,
            "text": self.text,
            "owner": self.owner.value,
            "speaker": self.speaker,
        }


class SubtitleTimingPolicy(Protocol):
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
                    owner=utterance.owner,
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
    provenance: Literal["verified"] = "verified"

    def subtitle_artifact_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "unit_id": self.unit_id,
            "variant": self.variant,
            "timing": self.timing,
            "adjustable": self.subtitles_adjustable,
            "basis": ArtifactBasisDescriptor.from_basis(self.subtitle_basis).to_dict(),
            "cues": [cue.to_dict() for cue in self.subtitles],
        }

    def subtitles_webvtt(self) -> str:
        return subtitles_webvtt(self.subtitles)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "provenance": self.provenance,
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
            "subtitles_webvtt": self.subtitles_webvtt(),
        }


@dataclass(frozen=True, slots=True)
class RawPresentationMedia:
    """Observed identity for media whose generation provenance is unavailable."""

    artifact_path: str
    version: int
    selection: MediaSelection
    content_digest: str
    actual_duration_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_path, str) or not self.artifact_path:
            raise ValueError("artifact_path must be a non-empty string")
        if type(self.version) is not int or self.version <= 0:
            raise ValueError("version must be a positive integer")
        if self.selection not in {"current", "history"}:
            raise ValueError("selection must be current or history")
        if not isinstance(self.content_digest, str) or _CONTENT_DIGEST_PATTERN.fullmatch(self.content_digest) is None:
            raise ValueError("content_digest must be a canonical sha256-v1 digest")
        duration = self.actual_duration_seconds
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration):
            raise ValueError("actual_duration_seconds must be positive and finite")
        if duration <= 0:
            raise ValueError("actual_duration_seconds must be positive and finite")
        object.__setattr__(self, "actual_duration_seconds", float(duration))

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_path": self.artifact_path,
            "version": self.version,
            "selection": self.selection,
            "currency": None,
            "basis": None,
            "content_digest": self.content_digest,
            "actual_duration_seconds": self.actual_duration_seconds,
        }


@dataclass(frozen=True, slots=True)
class RawVideoPresentationTrack:
    media: RawPresentationMedia
    start_microseconds: int
    duration_microseconds: int
    audio_enabled: bool = True
    gain: float = 1.0

    def to_dict(self) -> dict[str, object]:
        return {
            **self.media.to_dict(),
            "start_microseconds": self.start_microseconds,
            "duration_microseconds": self.duration_microseconds,
            "audio_enabled": self.audio_enabled,
            "gain": self.gain,
        }


@dataclass(frozen=True, slots=True)
class RawVideoPresentation:
    """Raw-only presentation for a manually uploaded video without typed provenance."""

    unit_id: str
    selection: MediaSelection
    video: RawVideoPresentationTrack
    variant: Literal["post_production"] = "post_production"
    speech_mode: None = None
    currency: None = None
    narration_audio: None = None
    subtitles: tuple[()] = ()
    subtitle_basis: None = None
    presentation_basis: None = None
    timing: None = None
    subtitles_adjustable: bool = False
    provenance: Literal["unavailable"] = "unavailable"

    def subtitle_artifact_dict(self) -> None:
        return None

    def subtitles_webvtt(self) -> None:
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "provenance": self.provenance,
            "unit_id": self.unit_id,
            "variant": self.variant,
            "speech_mode": self.speech_mode,
            "selection": self.selection,
            "currency": self.currency,
            "video": self.video.to_dict(),
            "narration_audio": None,
            "subtitles": [],
            "subtitle_basis": None,
            "presentation_basis": None,
            "timing": None,
            "subtitles_adjustable": False,
            "subtitles_webvtt": None,
        }


PresentationValue = SpeechPresentation | RawVideoPresentation


def materialize_raw_video_presentation(
    *,
    unit_id: str,
    video: RawPresentationMedia,
) -> RawVideoPresentation:
    """Represent an observed manual upload without inventing generation provenance."""

    if not isinstance(unit_id, str) or not unit_id:
        raise ValueError("unit_id must be a non-empty string")
    if not isinstance(video, RawPresentationMedia):
        raise TypeError("video must be RawPresentationMedia")
    return RawVideoPresentation(
        unit_id=unit_id,
        selection=video.selection,
        video=RawVideoPresentationTrack(
            media=video,
            start_microseconds=0,
            duration_microseconds=_duration_microseconds(video.actual_duration_seconds),
        ),
    )


def materialize_speech_presentation(
    preparation: SpeechPreparation,
    *,
    variant: RenditionVariant,
    video: PresentationMedia,
    provider_audio_enabled: bool,
    narration_audio: PresentationMedia | None = None,
    timing: SubtitleTimingPolicy | None = None,
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


def subtitles_webvtt(cues: tuple[SubtitleCue, ...]) -> str:
    lines = ["WEBVTT", ""]
    for index, cue in enumerate(cues, start=1):
        lines.extend(
            (
                str(index),
                f"{_vtt_timestamp(cue.start_microseconds)} --> {_vtt_timestamp(cue.end_microseconds)}",
                _webvtt_cue_text(cue.text),
                "",
            )
        )
    return "\n".join(lines)


def _webvtt_cue_text(text: str) -> str:
    return "\n".join(line for line in text.split("\n") if line.strip())


def _vtt_timestamp(microseconds: int) -> str:
    milliseconds = microseconds // 1_000
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


__all__ = [
    "MechanicalSubtitleTiming",
    "MediaCurrency",
    "MediaSelection",
    "NarrationPresentationTrack",
    "PresentationBoundaryError",
    "PresentationMedia",
    "PresentationProvenance",
    "PresentationValue",
    "RawPresentationMedia",
    "RawVideoPresentation",
    "RawVideoPresentationTrack",
    "SpeechPresentation",
    "SubtitleCue",
    "SubtitleTimingPolicy",
    "VideoPresentationTrack",
    "materialize_speech_presentation",
    "materialize_raw_video_presentation",
    "subtitles_webvtt",
]
