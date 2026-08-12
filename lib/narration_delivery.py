"""Per-request narration delivery and TTS artifact currency.

This module owns the transport-neutral contract between spoken-content ownership,
the current TTS configuration, and one unit's formal narration-audio artifact.  It
does not persist the user's delivery choice and does not submit media tasks.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactComparison,
    ArtifactKey,
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.audio_utils import probe_existing_audio_duration_seconds
from lib.path_safety import safe_join
from lib.reference_video.duration_slots import Adjustment, resolve_duration_slot
from lib.resource_paths import resource_relative_path
from lib.speech_composition import (
    SpeechFieldLocation,
    SpeechMode,
    SpeechOwner,
    SpeechPreparation,
)

POST_PRODUCTION = "post_production"
USE_TTS = "use_tts"
NarrationDelivery = Literal["post_production", "use_tts"]


@dataclass(frozen=True, slots=True)
class NarrationDeliveryRequestOptions:
    """Durable request facts; current TTS evidence is deliberately excluded."""

    narration_delivery: NarrationDelivery = POST_PRODUCTION
    confirmed_request_duration_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.narration_delivery not in (POST_PRODUCTION, USE_TTS):
            raise ValueError(f"unsupported narration delivery: {self.narration_delivery!r}")
        confirmed = self.confirmed_request_duration_seconds
        if confirmed is not None and (not isinstance(confirmed, int) or isinstance(confirmed, bool) or confirmed <= 0):
            raise ValueError("confirmed_request_duration_seconds must be a positive integer or null")

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"narration_delivery": self.narration_delivery}
        if self.confirmed_request_duration_seconds is not None:
            payload["confirmed_request_duration_seconds"] = self.confirmed_request_duration_seconds
        return payload

    @classmethod
    def from_payload(
        cls,
        payload: object,
        *,
        key: str = "narration_delivery_options",
    ) -> NarrationDeliveryRequestOptions:
        root = payload if isinstance(payload, dict) else {}
        raw = root.get(key)
        if not isinstance(raw, dict):
            return cls()
        delivery = raw.get("narration_delivery")
        if delivery not in (POST_PRODUCTION, USE_TTS):
            delivery = POST_PRODUCTION
        confirmed = raw.get("confirmed_request_duration_seconds")
        normalized = (
            confirmed if isinstance(confirmed, int) and not isinstance(confirmed, bool) and confirmed > 0 else None
        )
        return cls(
            narration_delivery=delivery,
            confirmed_request_duration_seconds=normalized,
        )


class NarrationTtsStatus(StrEnum):
    """Currency and usability of one unit's formal narration audio."""

    NOT_APPLICABLE = "not_applicable"
    NOT_CONFIGURED = "not_configured"
    MISSING = "missing"
    GENERATING = "generating"
    STALE = "stale"
    CURRENT = "current"
    UNMEASURABLE = "unmeasurable"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class TtsSynthesisSettings:
    """Resolved paid-synthesis inputs that participate in TTS currency."""

    provider_id: str
    model_id: str
    voice: str
    speed: float | None

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id must be non-empty")
        if not self.model_id.strip():
            raise ValueError("model_id must be non-empty")
        if not self.voice.strip():
            raise ValueError("voice must be non-empty")
        if self.speed is not None and (not math.isfinite(self.speed) or self.speed <= 0):
            raise ValueError("speed must be positive and finite or null")


class TtsSettingsResolver(Protocol):
    """Narrow configuration seam used by current-state delivery projection."""

    async def resolve_tts_synthesis_settings(self, project: dict) -> TtsSynthesisSettings:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class NarrationAudioEvidence:
    """Observed formal audio and manifest comparison for one resource."""

    comparison: ArtifactComparison
    present: bool
    duration_seconds: float | None


@dataclass(frozen=True, slots=True)
class NarrationDeliveryProblem:
    """Stable Web/Agent problem for a narration-delivery request."""

    code: str
    reason: str
    action: str
    locations: tuple[SpeechFieldLocation, ...]
    params: tuple[tuple[str, object], ...] = ()
    blocking: bool = True

    def parameters(self) -> dict[str, object]:
        return dict(self.params)

    def to_payload(self, *, unit_id: str) -> dict[str, object]:
        return {
            "code": self.code,
            "blocking": self.blocking,
            "unit_id": unit_id,
            "locations": [{"path": list(location.path), "line": location.line} for location in self.locations],
            "reason": self.reason,
            "action": self.action,
            "params": self.parameters(),
        }


@dataclass(frozen=True, slots=True)
class NarrationDeliveryPreparation:
    """Current request facts shared by Web, Agent, quote, queue, and worker."""

    delivery: NarrationDelivery
    unit_id: str
    speech_mode: SpeechMode | None
    tts_status: NarrationTtsStatus
    artifact_path: str
    basis_digest: str | None
    actual_duration_seconds: float | None
    problems: tuple[NarrationDeliveryProblem, ...]

    @property
    def allowed(self) -> bool:
        return not any(problem.blocking for problem in self.problems)

    @property
    def duration_floor(self) -> float | None:
        if (
            self.delivery == USE_TTS
            and self.tts_status is NarrationTtsStatus.CURRENT
            and self.actual_duration_seconds is not None
        ):
            return self.actual_duration_seconds
        return None

    def problem_payloads(self) -> list[dict[str, object]]:
        return [problem.to_payload(unit_id=self.unit_id) for problem in self.problems]

    def to_payload(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "kind": "narration_delivery",
            "delivery": self.delivery,
            "unit_id": self.unit_id,
            "speech_mode": self.speech_mode.value if self.speech_mode is not None else None,
            "tts_status": self.tts_status.value,
            "artifact_path": self.artifact_path,
            "basis_digest": self.basis_digest,
            "actual_duration_seconds": self.actual_duration_seconds,
            "duration_floor": self.duration_floor,
            "problems": self.problem_payloads(),
        }


@dataclass(frozen=True, slots=True)
class VideoRequestCostFacts:
    """Canonical provider request coordinates consumed by the shared quote seam."""

    provider_id: str
    model_id: str
    resolution: str | None
    duration_seconds: int
    generate_audio: bool


def video_request_cost_unavailable_problem(facts: VideoRequestCostFacts) -> NarrationDeliveryProblem:
    """Stable blocker used when a cross-tier request cannot be quoted exactly."""

    return _problem(
        "video_request_cost_unavailable",
        reason="video_request_cost_unavailable",
        action="retry_cost_estimate",
        path=("duration_seconds",),
        provider=facts.provider_id,
        model=facts.model_id,
        request_duration=facts.duration_seconds,
    )


@dataclass(frozen=True, slots=True)
class NarratedVideoDurationPreparation:
    """One storyboard video's delivery-aware duration request."""

    narration: NarrationDeliveryPreparation
    planned_duration_seconds: int
    duration_input: int | float
    request_duration_seconds: int | None
    adjustment: Adjustment | None
    problems: tuple[NarrationDeliveryProblem, ...]
    current_visual_duration_seconds: int | None = None
    cost: VideoRequestCostFacts | None = None

    @property
    def allowed(self) -> bool:
        return self.request_duration_seconds is not None and not any(problem.blocking for problem in self.problems)

    def problem_payloads(self) -> list[dict[str, object]]:
        return [problem.to_payload(unit_id=self.narration.unit_id) for problem in self.problems]

    def to_payload(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "kind": "narrated_video_duration",
            "unit_id": self.narration.unit_id,
            "narration_delivery": self.narration.to_payload(),
            "planned_duration": self.planned_duration_seconds,
            "duration_input": self.duration_input,
            "request_duration": self.request_duration_seconds,
            "adjustment": self.adjustment,
            "current_visual_duration": self.current_visual_duration_seconds,
            "problems": self.problem_payloads(),
        }


class NarrationDeliveryBlockedError(ValueError):
    """Execution-time rejection preserving the canonical problem envelope."""

    def __init__(self, preparation: NarrationDeliveryPreparation) -> None:
        if preparation.allowed:
            raise ValueError("narration delivery error requires a blocking preparation")
        self.preparation = preparation
        codes = ", ".join(problem.code for problem in preparation.problems if problem.blocking)
        super().__init__(f"unit {preparation.unit_id} narration delivery blocked: {codes}")


class NarratedVideoDurationBlockedError(ValueError):
    """Execution-time rejection for a delivery-aware storyboard video request."""

    def __init__(self, preparation: NarratedVideoDurationPreparation) -> None:
        if preparation.allowed:
            raise ValueError("narrated video duration error requires a blocking preparation")
        self.preparation = preparation
        problem = next(problem for problem in preparation.problems if problem.blocking)
        self.code = problem.code
        self.params = problem.parameters()
        codes = ", ".join(problem.code for problem in preparation.problems if problem.blocking)
        super().__init__(f"unit {preparation.narration.unit_id} narrated video blocked: {codes}")


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


def _problem(
    code: str,
    *,
    reason: str,
    action: str,
    path: tuple[str | int, ...],
    **params: object,
) -> NarrationDeliveryProblem:
    return NarrationDeliveryProblem(
        code=code,
        reason=reason,
        action=action,
        locations=(SpeechFieldLocation(path),),
        params=tuple(params.items()),
    )


def _speech_problems(preparation: SpeechPreparation) -> list[NarrationDeliveryProblem]:
    return [
        NarrationDeliveryProblem(
            code=problem.code.value,
            reason=problem.reason.value,
            action=problem.action.value,
            locations=problem.locations,
        )
        for problem in preparation.problems
    ]


def prepare_narration_delivery(
    *,
    delivery: NarrationDelivery,
    preparation: SpeechPreparation,
    artifact_path: str,
    settings: TtsSynthesisSettings | None,
    evidence: NarrationAudioEvidence | None,
    tts_in_progress: bool = False,
) -> NarrationDeliveryPreparation:
    """Project current TTS currency into one non-persistent delivery request."""

    if delivery not in (POST_PRODUCTION, USE_TTS):
        raise ValueError(f"unsupported narration delivery: {delivery!r}")

    problems = _speech_problems(preparation)
    basis_digest: str | None = None
    actual_duration: float | None = None

    if preparation.mode is not SpeechMode.NARRATOR_VOICEOVER:
        status = NarrationTtsStatus.NOT_APPLICABLE
        if delivery == USE_TTS and not problems:
            problems.append(
                _problem(
                    "tts_not_applicable",
                    reason="unit_has_no_narrator_voiceover",
                    action="choose_post_production",
                    path=("narration_delivery",),
                )
            )
    elif settings is None:
        status = NarrationTtsStatus.NOT_CONFIGURED
        if delivery == USE_TTS:
            problems.append(
                _problem(
                    "tts_not_configured",
                    reason="tts_provider_unavailable",
                    action="configure_tts",
                    path=("generation_settings", "audio_backend"),
                )
            )
    else:
        basis = build_narration_audio_basis(preparation, settings)
        basis_digest = basis.digest
        comparison = evidence.comparison if evidence is not None else None
        if comparison is not None and comparison.status is ArtifactStatus.BLOCKED:
            status = NarrationTtsStatus.BLOCKED
            if delivery == USE_TTS:
                problems.append(
                    _problem(
                        "tts_state_unavailable",
                        reason="tts_artifact_state_blocked",
                        action="repair_tts_state",
                        path=("generated_assets", "narration_audio"),
                    )
                )
        elif evidence is None or not evidence.present:
            status = NarrationTtsStatus.MISSING
            if delivery == USE_TTS:
                problems.append(
                    _problem(
                        "tts_missing",
                        reason="current_tts_audio_missing",
                        action="generate_tts",
                        path=("generated_assets", "narration_audio"),
                    )
                )
        elif comparison is None or comparison.status is not ArtifactStatus.CURRENT:
            status = NarrationTtsStatus.STALE
            if delivery == USE_TTS:
                problems.append(
                    _problem(
                        "tts_stale",
                        reason="tts_basis_changed",
                        action="regenerate_tts",
                        path=("generated_assets", "narration_audio"),
                    )
                )
        else:
            measured = evidence.duration_seconds
            if measured is None or not math.isfinite(measured) or measured <= 0:
                status = NarrationTtsStatus.UNMEASURABLE
                if delivery == USE_TTS:
                    problems.append(
                        _problem(
                            "tts_duration_unavailable",
                            reason="tts_media_duration_unavailable",
                            action="repair_tts_audio",
                            path=("generated_assets", "narration_audio"),
                        )
                    )
            else:
                status = NarrationTtsStatus.CURRENT
                actual_duration = measured

    if tts_in_progress and status is not NarrationTtsStatus.CURRENT:
        status = NarrationTtsStatus.GENERATING
        actual_duration = None
        replaceable_codes = {"tts_missing", "tts_stale", "tts_duration_unavailable"}
        problems = [problem for problem in problems if problem.code not in replaceable_codes]
        if delivery == USE_TTS and not problems:
            problems.append(
                _problem(
                    "tts_generating",
                    reason="tts_generation_in_progress",
                    action="wait_for_tts",
                    path=("generated_assets", "narration_audio"),
                )
            )

    return NarrationDeliveryPreparation(
        delivery=delivery,
        unit_id=preparation.unit_id,
        speech_mode=preparation.mode,
        tts_status=status,
        artifact_path=artifact_path,
        basis_digest=basis_digest,
        actual_duration_seconds=actual_duration,
        problems=tuple(problems),
    )


def prepare_narrated_video_duration(
    *,
    narration: NarrationDeliveryPreparation,
    planned_duration_seconds: int,
    supported_durations: Sequence[int],
    confirmed_request_duration_seconds: int | None,
    current_visual_duration_seconds: int | None = None,
) -> NarratedVideoDurationPreparation:
    """Apply a current TTS duration floor to one storyboard video tier."""

    if isinstance(planned_duration_seconds, bool) or planned_duration_seconds <= 0:
        raise ValueError("planned_duration_seconds must be a positive integer")
    if current_visual_duration_seconds is not None and (
        isinstance(current_visual_duration_seconds, bool) or current_visual_duration_seconds <= 0
    ):
        raise ValueError("current_visual_duration_seconds must be a positive integer or null")
    durations = tuple(
        sorted({duration for duration in supported_durations if not isinstance(duration, bool) and duration > 0})
    )
    duration_input: int | float = max(
        planned_duration_seconds,
        narration.duration_floor or 0,
    )
    problems = list(narration.problems)
    if not durations:
        problems.append(
            _problem(
                "video_supported_durations_missing",
                reason="video_duration_tiers_unavailable",
                action="configure_video_model",
                path=("duration_seconds",),
            )
        )
        return NarratedVideoDurationPreparation(
            narration=narration,
            planned_duration_seconds=planned_duration_seconds,
            duration_input=duration_input,
            request_duration_seconds=None,
            adjustment=None,
            problems=tuple(problems),
            current_visual_duration_seconds=current_visual_duration_seconds,
        )

    slot = resolve_duration_slot(duration_input, durations)
    request_duration: int | None = slot.seconds
    if not problems:
        if slot.adjustment == "down":
            request_duration = None
            problems.append(
                _problem(
                    "needs_replan",
                    reason="request_duration_exceeds_maximum",
                    action="replan_unit",
                    path=("duration_seconds",),
                    duration_input=duration_input,
                    maximum_duration=slot.seconds,
                )
            )
        elif (
            slot.seconds != (current_visual_duration_seconds or planned_duration_seconds)
            and confirmed_request_duration_seconds != slot.seconds
        ):
            problems.append(
                _problem(
                    "reference_duration_confirmation_required",
                    reason="request_duration_uses_different_tier",
                    action="confirm_duration",
                    path=("duration_seconds",),
                    script_duration=planned_duration_seconds,
                    duration_input=duration_input,
                    request_duration=slot.seconds,
                    adjustment=slot.adjustment,
                    current_visual_duration=current_visual_duration_seconds,
                )
            )
    return NarratedVideoDurationPreparation(
        narration=narration,
        planned_duration_seconds=planned_duration_seconds,
        duration_input=duration_input,
        request_duration_seconds=request_duration,
        adjustment=slot.adjustment,
        problems=tuple(problems),
        current_visual_duration_seconds=current_visual_duration_seconds,
    )


def prepare_narrated_video_output(
    preparation: NarratedVideoDurationPreparation,
    *,
    actual_duration_seconds: float | None,
) -> NarratedVideoDurationPreparation:
    """Validate that generated media can carry the selected current TTS in full."""

    if preparation.narration.delivery != USE_TTS or not preparation.allowed:
        return preparation
    tts_duration = preparation.narration.actual_duration_seconds
    if tts_duration is None:
        raise ValueError("allowed TTS video request is missing its actual narration duration")
    if actual_duration_seconds is None or not math.isfinite(actual_duration_seconds) or actual_duration_seconds <= 0:
        problem = _problem(
            "video_duration_unavailable",
            reason="generated_video_duration_unavailable",
            action="regenerate_video",
            path=("generated_assets", "video_clip"),
            tts_duration=tts_duration,
        )
    elif actual_duration_seconds < tts_duration:
        problem = _problem(
            "video_shorter_than_tts",
            reason="generated_video_shorter_than_current_tts",
            action="regenerate_video",
            path=("generated_assets", "video_clip"),
            video_duration=actual_duration_seconds,
            tts_duration=tts_duration,
        )
    else:
        return preparation
    return replace(preparation, problems=(*preparation.problems, problem))


async def resolve_tts_synthesis_settings(
    project: dict,
    resolver: TtsSettingsResolver,
) -> TtsSynthesisSettings:
    """Resolve the effective provider/model, voice, and speed for a paid TTS call."""

    return await resolver.resolve_tts_synthesis_settings(project)


async def prepare_current_narration_delivery(
    *,
    project: dict,
    episode: int,
    preparation: SpeechPreparation,
    project_path: Path,
    delivery: NarrationDelivery,
    resolver: TtsSettingsResolver,
    duration_probe: Callable[[Path], Awaitable[float | None]] = probe_existing_audio_duration_seconds,
    tts_in_progress: bool = False,
) -> NarrationDeliveryPreparation:
    """Read the current configuration, manifest, and formal media for one request.

    Post-production requests deliberately return before configuration or filesystem
    access.  A caller that wants to display TTS readiness asks for ``use_tts``.
    """

    artifact_path = resource_relative_path("audio", preparation.unit_id)
    if delivery == POST_PRODUCTION:
        return prepare_narration_delivery(
            delivery=delivery,
            preparation=preparation,
            artifact_path=artifact_path,
            settings=None,
            evidence=None,
        )
    if preparation.problems or preparation.mode is not SpeechMode.NARRATOR_VOICEOVER:
        return prepare_narration_delivery(
            delivery=delivery,
            preparation=preparation,
            artifact_path=artifact_path,
            settings=None,
            evidence=None,
        )
    try:
        settings = await resolve_tts_synthesis_settings(project, resolver)
    except ValueError:
        return prepare_narration_delivery(
            delivery=delivery,
            preparation=preparation,
            artifact_path=artifact_path,
            settings=None,
            evidence=None,
        )

    basis = build_narration_audio_basis(preparation, settings)
    adapter = ProjectArtifactManifestAdapter(project_path)
    comparison = ArtifactManifest(adapter).compare(
        ArtifactKey.episode_audio(episode, preparation.unit_id),
        artifact_path=artifact_path,
        basis=basis,
    )
    observation = adapter.inspect_artifact(artifact_path)
    duration: float | None = None
    if comparison.status is ArtifactStatus.CURRENT and observation.present:
        duration = await duration_probe(safe_join(project_path, artifact_path, require_file=True))
    return prepare_narration_delivery(
        delivery=delivery,
        preparation=preparation,
        artifact_path=artifact_path,
        settings=settings,
        evidence=NarrationAudioEvidence(
            comparison=comparison,
            present=observation.present,
            duration_seconds=duration,
        ),
        tts_in_progress=tts_in_progress,
    )


async def prepare_current_narrated_video_duration(
    *,
    project: dict,
    episode: int,
    preparation: SpeechPreparation,
    project_path: Path,
    delivery: NarrationDelivery,
    planned_duration_seconds: int,
    supported_durations: Sequence[int],
    confirmed_request_duration_seconds: int | None,
    resolver: TtsSettingsResolver,
    tts_in_progress: bool = False,
    current_visual_duration_seconds: int | None = None,
) -> NarratedVideoDurationPreparation:
    """Materialize one storyboard video's current delivery and duration facts."""

    narration = await prepare_current_narration_delivery(
        project=project,
        episode=episode,
        preparation=preparation,
        project_path=project_path,
        delivery=delivery,
        resolver=resolver,
        tts_in_progress=tts_in_progress,
    )
    return prepare_narrated_video_duration(
        narration=narration,
        planned_duration_seconds=planned_duration_seconds,
        supported_durations=supported_durations,
        confirmed_request_duration_seconds=confirmed_request_duration_seconds,
        current_visual_duration_seconds=current_visual_duration_seconds,
    )


def register_narration_audio(
    *,
    project_path: Path,
    episode: int,
    preparation: SpeechPreparation,
    settings: TtsSynthesisSettings,
) -> ArtifactBasis:
    """Register a successfully promoted formal TTS artifact for one unit."""

    artifact_path = resource_relative_path("audio", preparation.unit_id)
    basis = build_narration_audio_basis(preparation, settings)
    ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register(
        ArtifactKey.episode_audio(episode, preparation.unit_id),
        artifact_path=artifact_path,
        basis=basis,
    )
    return basis


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
    "NarrationAudioEvidence",
    "NarrationDelivery",
    "NarrationDeliveryBlockedError",
    "NarrationDeliveryPreparation",
    "NarrationDeliveryProblem",
    "NarrationDeliveryRequestOptions",
    "NarrationTtsStatus",
    "NarratedVideoDurationPreparation",
    "NarratedVideoDurationBlockedError",
    "TtsSettingsResolver",
    "TtsSynthesisSettings",
    "VideoRequestCostFacts",
    "build_narration_audio_basis",
    "canonical_narration_text",
    "prepare_current_narration_delivery",
    "prepare_current_narrated_video_duration",
    "prepare_narrated_video_duration",
    "prepare_narrated_video_output",
    "prepare_narration_delivery",
    "register_narration_audio",
    "register_narration_audio_transactionally",
    "resolve_tts_synthesis_settings",
]
