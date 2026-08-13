"""Self-verifying typed currency facts for one paid video request."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Self, cast

from lib.artifact_manifest import ArtifactBasis, ArtifactBasisDescriptor, compose_video_artifact_basis
from lib.asset_types import asset_name_comparison_key
from lib.speech_artifact_provenance import build_video_duration_basis

_SCHEMA_VERSION = 1
_STORYBOARD_VISUAL_KIND = "artifact-visual/video-storyboard"
_REFERENCE_VISUAL_KIND = "artifact-visual/video-reference"
_SPEECH_KIND = "artifact-speech/video"
_DURATION_KIND = "artifact-speech/video-duration"
_VIDEO_KIND = "artifact-components/video"
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_BASIS_DIGEST = re.compile(r"sha256-v1:[0-9a-f]{64}\Z")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True, slots=True)
class VideoArtifactCurrencyFacts:
    """Complete execution-frozen facts required to select or restore a video.

    Descriptors alone cannot prove which canonical inputs produced their digest.
    Each component therefore carries its normalized input document, while the
    aggregate digest protects the route knobs and selection parent token as one
    value.  Consumers parse this object once and use its derived descriptors.
    """

    episode: int
    request_duration_seconds: int
    visual_basis: ArtifactBasis
    speech_basis: ArtifactBasis
    duration_basis: ArtifactBasis
    video_basis: ArtifactBasis
    voice_style_speakers: tuple[str, ...]
    duration_tiers: tuple[int, ...]
    reference_image_limit: int | None
    parent_version: int

    _FIELDS = frozenset(
        {
            "schema_version",
            "episode",
            "request_duration_seconds",
            "visual_basis",
            "speech_basis",
            "duration_basis",
            "video_basis",
            "voice_style_speakers",
            "duration_tiers",
            "reference_image_limit",
            "parent_version",
            "currency_digest",
        }
    )

    def __post_init__(self) -> None:
        if type(self.episode) is not int or self.episode < 1:
            raise ValueError("video artifact episode must be a positive integer")
        if type(self.request_duration_seconds) is not int or self.request_duration_seconds <= 0:
            raise ValueError("video artifact request duration must be a positive integer")
        for field, value in (
            ("visual_basis", self.visual_basis),
            ("speech_basis", self.speech_basis),
            ("duration_basis", self.duration_basis),
            ("video_basis", self.video_basis),
        ):
            if not isinstance(value, ArtifactBasis):
                raise TypeError(f"{field} must be an ArtifactBasis")
            if value.kind_version != 1:
                raise ValueError(f"{field} must use the supported kind version")
        if self.visual_basis.kind not in {_STORYBOARD_VISUAL_KIND, _REFERENCE_VISUAL_KIND}:
            raise ValueError("visual_basis does not describe a supported video route")
        _validate_visual_inputs(self.visual_basis)
        if self.speech_basis.kind != _SPEECH_KIND:
            raise ValueError("speech_basis does not describe video speech")
        _validate_speech_inputs(self.speech_basis)
        expected_duration = build_video_duration_basis(self.request_duration_seconds)
        if self.duration_basis != expected_duration:
            raise ValueError("duration_basis does not describe the paid request tier")
        expected_video = compose_video_artifact_basis(
            visual=self.visual_basis,
            speech=self.speech_basis,
            duration=self.duration_basis,
        )
        if self.video_basis != expected_video or self.video_basis.kind != _VIDEO_KIND:
            raise ValueError("video_basis does not compose the frozen video components")
        if any(
            not isinstance(speaker, str) or not speaker or asset_name_comparison_key(speaker) != speaker
            for speaker in self.voice_style_speakers
        ):
            raise ValueError("voice_style_speakers must contain canonical non-empty names")
        if len(set(self.voice_style_speakers)) != len(self.voice_style_speakers):
            raise ValueError("voice_style_speakers must be unique")
        if (
            not self.duration_tiers
            or tuple(sorted(set(self.duration_tiers))) != self.duration_tiers
            or any(type(tier) is not int or tier <= 0 for tier in self.duration_tiers)
            or self.request_duration_seconds not in self.duration_tiers
        ):
            raise ValueError("duration_tiers must be sorted positive tiers containing the paid request tier")
        limit = self.reference_image_limit
        if self.visual_basis.kind == _STORYBOARD_VISUAL_KIND:
            if limit is not None:
                raise ValueError("storyboard video facts cannot carry a reference-image limit")
        elif limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("reference video facts require an unlimited or non-negative reference-image limit")
        if type(self.parent_version) is not int or self.parent_version < 0:
            raise ValueError("parent_version must be a non-negative current-version token")

    @property
    def visual_descriptor(self) -> ArtifactBasisDescriptor:
        return ArtifactBasisDescriptor.from_basis(self.visual_basis)

    @property
    def speech_descriptor(self) -> ArtifactBasisDescriptor:
        return ArtifactBasisDescriptor.from_basis(self.speech_basis)

    @property
    def duration_descriptor(self) -> ArtifactBasisDescriptor:
        return ArtifactBasisDescriptor.from_basis(self.duration_basis)

    @property
    def video_descriptor(self) -> ArtifactBasisDescriptor:
        return ArtifactBasisDescriptor.from_basis(self.video_basis)

    @property
    def currency_digest(self) -> str:
        payload = self._payload()
        return f"sha256-v1:{hashlib.sha256(_canonical_json(payload).encode('utf-8')).hexdigest()}"

    def _payload(self) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "episode": self.episode,
            "request_duration_seconds": self.request_duration_seconds,
            "visual_basis": self.visual_basis.to_evidence_dict(),
            "speech_basis": self.speech_basis.to_evidence_dict(),
            "duration_basis": self.duration_basis.to_evidence_dict(),
            "video_basis": self.video_basis.to_evidence_dict(),
            "voice_style_speakers": list(self.voice_style_speakers),
            "duration_tiers": list(self.duration_tiers),
            "reference_image_limit": self.reference_image_limit,
            "parent_version": self.parent_version,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._payload(), "currency_digest": self.currency_digest}

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, Mapping) or set(value) != cls._FIELDS:
            raise ValueError("video artifact currency facts have an invalid schema")
        raw = cast(Mapping[str, Any], value)
        if raw["schema_version"] != _SCHEMA_VERSION:
            raise ValueError("unsupported video artifact currency schema")
        speakers = raw["voice_style_speakers"]
        tiers = raw["duration_tiers"]
        if not isinstance(speakers, list) or not isinstance(tiers, list):
            raise ValueError("video artifact currency collections must be arrays")
        try:
            facts = cls(
                episode=raw["episode"],
                request_duration_seconds=raw["request_duration_seconds"],
                visual_basis=ArtifactBasis.from_evidence_dict(raw["visual_basis"]),
                speech_basis=ArtifactBasis.from_evidence_dict(raw["speech_basis"]),
                duration_basis=ArtifactBasis.from_evidence_dict(raw["duration_basis"]),
                video_basis=ArtifactBasis.from_evidence_dict(raw["video_basis"]),
                voice_style_speakers=tuple(speakers),
                duration_tiers=tuple(tiers),
                reference_image_limit=raw["reference_image_limit"],
                parent_version=raw["parent_version"],
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("video artifact currency facts are not self-verifying") from exc
        if raw["currency_digest"] != facts.currency_digest:
            raise ValueError("video artifact currency digest does not match its canonical facts")
        return facts


__all__ = ["VideoArtifactCurrencyFacts"]


def _basis_inputs(basis: ArtifactBasis) -> Mapping[str, Any]:
    evidence = basis.to_evidence_dict()
    inputs = evidence.get("inputs")
    if not isinstance(inputs, Mapping):  # pragma: no cover - ArtifactBasis invariant
        raise ValueError("artifact basis inputs must be an object")
    return cast(Mapping[str, Any], inputs)


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _canvas_is_valid(value: object) -> bool:
    return isinstance(value, Mapping) and set(value) == {"aspect_ratio"} and _nonempty(value["aspect_ratio"])


def _validate_visual_inputs(basis: ArtifactBasis) -> None:
    inputs = _basis_inputs(basis)
    if basis.kind == _STORYBOARD_VISUAL_KIND:
        if set(inputs) != {"resource_id", "visual_prompt", "canvas", "frames"}:
            raise ValueError("storyboard visual basis has invalid canonical inputs")
        prompt = inputs["visual_prompt"]
        prompt_valid = (_nonempty(prompt)) or (
            isinstance(prompt, Mapping)
            and set(prompt) == {"action", "camera_motion"}
            and _nonempty(prompt["action"])
            and isinstance(prompt["camera_motion"], str)
        )
        frames = inputs["frames"]
        frames_valid = (
            isinstance(frames, list)
            and len(frames) in {1, 2}
            and [frame.get("role") for frame in frames if isinstance(frame, Mapping)]
            == (["storyboard"] if len(frames) == 1 else ["storyboard", "end_frame"])
            and all(
                isinstance(frame, Mapping)
                and set(frame) == {"role", "sha256"}
                and isinstance(frame["sha256"], str)
                and _HEX_DIGEST.fullmatch(frame["sha256"]) is not None
                for frame in frames
            )
        )
        if not _nonempty(inputs["resource_id"]) or not prompt_valid or not _canvas_is_valid(inputs["canvas"]):
            raise ValueError("storyboard visual basis has invalid canonical inputs")
        if not frames_valid:
            raise ValueError("storyboard visual basis has invalid frame evidence")
        return

    if set(inputs) != {"unit_id", "visual_shots", "style", "canvas", "request_references"}:
        raise ValueError("reference visual basis has invalid canonical inputs")
    shots = inputs["visual_shots"]
    shots_valid = isinstance(shots, list) and all(
        isinstance(shot, Mapping)
        and set(shot) == {"shot_index", "lines"}
        and shot["shot_index"] == index
        and isinstance(shot["lines"], list)
        and bool(shot["lines"])
        and all(_nonempty(line) for line in shot["lines"])
        for index, shot in enumerate(shots)
    )
    references = inputs["request_references"]
    references_valid = isinstance(references, list) and all(_reference_evidence_is_valid(item) for item in references)
    if (
        not _nonempty(inputs["unit_id"])
        or not isinstance(inputs["style"], str)
        or not _canvas_is_valid(inputs["canvas"])
        or not shots_valid
        or not references_valid
    ):
        raise ValueError("reference visual basis has invalid canonical inputs")


def _reference_evidence_is_valid(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    allowed = {"role", "sha256", "logical_identity", "kind"}
    identity = value.get("logical_identity")
    return (
        set(value) <= allowed
        and {"role", "sha256", "logical_identity"} <= set(value)
        and value["role"] == "reference_image"
        and isinstance(value["sha256"], str)
        and _HEX_DIGEST.fullmatch(value["sha256"]) is not None
        and isinstance(identity, Mapping)
        and set(identity) == {"type", "id"}
        and _nonempty(identity["type"])
        and _nonempty(identity["id"])
        and ("kind" not in value or _nonempty(value["kind"]))
    )


def _validate_speech_inputs(basis: ArtifactBasis) -> None:
    inputs = _basis_inputs(basis)
    mode = inputs.get("mode")
    if mode in {"silent", "narrator_voiceover"}:
        if set(inputs) != {"mode"}:
            raise ValueError("non-character video speech basis has invalid canonical inputs")
        return
    if mode != "character_speech" or set(inputs) != {"mode", "utterances", "voices"}:
        raise ValueError("video speech basis has invalid canonical inputs")
    utterances = inputs["utterances"]
    voices = inputs["voices"]
    if not isinstance(utterances, list) or not utterances or not isinstance(voices, list):
        raise ValueError("character video speech basis requires utterances and voices")
    speaker_order: list[str] = []
    for utterance in utterances:
        if (
            not isinstance(utterance, Mapping)
            or set(utterance) != {"speaker", "text"}
            or not _nonempty(utterance["speaker"])
            or asset_name_comparison_key(utterance["speaker"]) != utterance["speaker"]
            or not _nonempty(utterance["text"])
        ):
            raise ValueError("character video speech utterance is not canonical")
        if utterance["speaker"] not in speaker_order:
            speaker_order.append(utterance["speaker"])
    if len(voices) != len(speaker_order):
        raise ValueError("character video speech voices do not match ordered speakers")
    for speaker, voice in zip(speaker_order, voices, strict=True):
        digest = voice.get("reference_audio_digest") if isinstance(voice, Mapping) else object()
        if (
            not isinstance(voice, Mapping)
            or set(voice) != {"speaker", "voice_style", "reference_audio_digest"}
            or voice["speaker"] != speaker
            or not isinstance(voice["voice_style"], str)
            or (digest is not None and (not isinstance(digest, str) or _BASIS_DIGEST.fullmatch(digest) is None))
        ):
            raise ValueError("character video speech voice evidence is not canonical")
