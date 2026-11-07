"""Video request facts: execution facts for one video-generation request, evaluated once per request (``docs/adr/0086``).

Evaluation dimensions are project × route × task-type bucket × identity source. Readers (quotes, preflight, UI, Agent) use execution models
resolved from current configuration; execution uses actual lane-constructed backend identities (``docs/adr/0049`` ③). Apart from identity
sources, evaluation is identical on both sides; consumers read resolutions, tiers and audio tracks directly from results.

- Unset resolutions are not sent: request resolutions are those used in duration-linked constraints, without fallback tiers.
- Narrow strictly: empty intersections fail, never fall back to unrestricted sets.
- Failures carry types only (issue codes, parameters and remediation), without degradation/fallback; consumers handle them by stage.

This layer is route-neutral: no reference-video subpackage or server dependencies (``pyproject.toml`` import-linter contract).
Never bind database sessions at module level; callers inject resolvers. Execution injects the resolver already bound to GenerationContext's
session.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError

from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.resolver import (
    ConfigResolver,
    DurationExclusionReason,
    VideoBucketCapabilityError,
    VideoGenerationType,
    VideoSupportedDurationsError,
    VoiceConsistency,
    apply_duration_constraints,
)

#: Video routes: storyboard (first-frame-driven) and reference video. Issue codes form route-specific families; never rename recorded codes.
VideoRoute = Literal["storyboard", "reference_video"]

#: Evaluation-failure remediation: all points to video-model configuration.
CONFIGURE_VIDEO_MODEL_ACTION = "configure_video_model"

#: Duration tiers borrowed for script planning when model-row durations are endpoint-fixed.
#:
#: These do not mean supported model seconds: ArcReel does not drive that dimension; workflow determines output length. They only provide script
#: planning volume guidance for approximate shot length; without it, storyboards cannot split. Borrow only within planning
#: (:func:`planning_durations`); UI and Agent payloads still receive empty sets plus endpoint-fixed flags. Values match
#: ``lib.custom_provider.duration_presets.DEFAULT_FALLBACK`` at ``[4, 8]``, with different semantics (the latter is the
#: conservative custom-provider write-layer default), so do not import from there.
ENDPOINT_FIXED_PLANNING_DURATIONS: list[int] = [4, 8]

#: Planning baseline when neither unit duration nor project preferred duration exists: longest borrowed script-planning tier.
#:
#: Not any model's available tier. Shared origins with :data:`ENDPOINT_FIXED_PLANNING_DURATIONS` make endpoint-fixed
#: units plan the same volume across storyboard/reference routes and TTS/non-TTS paths.
DEFAULT_PLANNED_DURATION_SECONDS = max(ENDPOINT_FIXED_PLANNING_DURATIONS)

_FailureKind = Literal["missing", "invalid", "incompatible", "unavailable"]

_ROUTE_FAILURE_CODES: dict[VideoRoute, dict[_FailureKind, str]] = {
    "storyboard": {
        "missing": "video_supported_durations_missing",
        "invalid": "video_supported_durations_invalid",
        "incompatible": "video_supported_durations_incompatible",
        "unavailable": "video_capability_unavailable",
    },
    "reference_video": {
        "missing": "reference_supported_durations_missing",
        "invalid": "reference_supported_durations_invalid",
        "incompatible": "reference_supported_durations_incompatible",
        "unavailable": "reference_capability_unavailable",
    },
}


@dataclass(frozen=True)
class ConfiguredVideoIdentity:
    """Reader identity: resolve bucket execution models from current configuration, passing bucket-capability gates."""


@dataclass(frozen=True)
class ExecutionVideoIdentity:
    """Execution identity: canonical ``provider_id`` and actual ``.model`` of lane-constructed backends."""

    provider_id: str
    model_id: str


CONFIGURED_VIDEO_IDENTITY = ConfiguredVideoIdentity()

VideoRequestIdentity = ConfiguredVideoIdentity | ExecutionVideoIdentity


@dataclass(frozen=True)
class ResolutionOverride:
    """Preview unsaved resolutions, replacing persisted project tiers (``None`` = form Auto, resolved as no saved project tier).

    Only settings pages and creation wizards preview edited configuration; execution and other readers omit this, evaluating persisted configuration.
    """

    resolution: str | None


@dataclass(frozen=True)
class VideoRequestFacts:
    """Execution facts for one video request.

    None ``resolution`` means no request resolution parameter. ``supported_durations`` is the full model-declared set;
    ``allowed_durations`` narrows by request resolution and reference images; ``excluded_durations`` explains every
    excluded duration. With endpoint-fixed durations (``docs/adr/0082``), all three are valid empty sets.

    Four audio facts: ``requested_generate_audio`` is the user switch; ``generate_audio`` indicates audio-track presence for
    pricing; ``has_audio_track`` indicates output audio-track presence; ``audio_switch_controllable`` indicates switch control.
    Pricing requires execution models, ``resolution``, requested seconds and ``generate_audio``.

    Request-shape capabilities come from the same capability composition, read directly by assembly/projection: ``max_reference_images`` is the effective
    per-request reference-image limit (None = uncataloged, no trimming; 0 = refused); ``text_to_video`` / ``first_frame`` govern imageless requests;
    ``voice_consistency`` is the voice-consistency tier; ``max_reference_audio_count`` and
    ``reference_audio_per_image`` describe reference-audio segment limits (None = uncataloged) and whether segments attach to reference images individually.
    """

    route: VideoRoute
    generation_type: VideoGenerationType
    provider_id: str
    model_id: str
    resolution: str | None
    supported_durations: tuple[int, ...]
    allowed_durations: tuple[int, ...]
    excluded_durations: tuple[tuple[int, DurationExclusionReason], ...]
    duration_endpoint_fixed: bool
    requested_generate_audio: bool
    generate_audio: bool
    has_audio_track: bool
    audio_switch_controllable: bool
    max_reference_images: int | None
    text_to_video: bool
    first_frame: bool
    voice_consistency: VoiceConsistency
    max_reference_audio_count: int | None
    reference_audio_per_image: bool


@dataclass(frozen=True, slots=True)
class VideoRequestCostFacts:
    """Shared quote provider-request coordinates: video request facts plus requested seconds and request shape.

    ``aspect_ratio`` is project video aspect ratio; ``has_first_frame`` indicates requests carry first frames (storyboard image-to-video);
    ``reference_image_count`` counts sent reference images (reference video). All three share execution request sources; presubmission estimates
    reconstruct hypothetical provider requests (:meth:`provider_request`), passed to channels for usage-contract metering.
    """

    request_facts: VideoRequestFacts
    duration_seconds: int
    aspect_ratio: str
    has_first_frame: bool = False
    reference_image_count: int = 0

    def provider_request(self) -> VideoGenerationRequest:
        """Hypothetical request shape sent to channels at execution: images are placeholders, without file reads, for metering only.

        Storyboard image-to-video first frames are storyboard images generated at project aspect ratios. Channels requiring upstream first-frame
        aspect-ratio adaptation (``first_frame_ratio_adaptive_only``) output that storyboard-image ratio, so use project ratios here directly.
        With output audio tracks but uncontrollable switches, upstream always outputs sound; write the audio switch as enabled.
        """
        facts = self.request_facts
        return VideoGenerationRequest(
            prompt="",
            output_path=Path("estimate.mp4"),
            aspect_ratio=self.aspect_ratio,
            duration_seconds=self.duration_seconds,
            resolution=self.request_facts.resolution,
            start_image=Path("first-frame.png") if self.has_first_frame else None,
            reference_images=[Path(f"reference-{index}.png") for index in range(self.reference_image_count)] or None,
            generate_audio=facts.requested_generate_audio
            or (facts.has_audio_track and not facts.audio_switch_controllable),
        )

    @property
    def provider_id(self) -> str:
        return self.request_facts.provider_id

    @property
    def model_id(self) -> str:
        return self.request_facts.model_id

    @property
    def resolution(self) -> str | None:
        return self.request_facts.resolution

    @property
    def generate_audio(self) -> bool:
        return self.request_facts.generate_audio


@dataclass(frozen=True)
class VideoRequestFactsFailure:
    """Evaluation failure: issue codes, rendering parameters and remediation. Parameter keys are persisted contracts; append only, never change."""

    code: str
    params: tuple[tuple[str, object], ...] = ()
    action: str = CONFIGURE_VIDEO_MODEL_ACTION

    def parameters(self) -> dict[str, object]:
        return dict(self.params)

    def summary(self) -> str:
        """One-line issue-code/rendering-parameter summary (``code（k=v, …）``), for Agent receipts and draft-contract violation copy."""
        params = ", ".join(f"{key}={value}" for key, value in self.params)
        return f"{self.code}（{params}）" if params else self.code

    def problem_payload(self) -> dict[str, object]:
        """Reader issue envelopes: issue codes, rendering parameters and remediation, identical in Web and Agent payloads."""
        return {"code": self.code, "params": self.parameters(), "action": self.action}


class VideoRequestFactsError(ValueError):
    """Raised when consumers requiring successful facts receive failures; ``code`` / ``params`` feed structured errors and failure encoding directly."""

    def __init__(self, failure: VideoRequestFactsFailure) -> None:
        self.failure = failure
        super().__init__(failure.code)

    @property
    def code(self) -> str:
        return self.failure.code

    @property
    def params(self) -> dict[str, object]:
        return self.failure.parameters()


def require_video_request_facts(result: VideoRequestFacts | VideoRequestFactsFailure) -> VideoRequestFacts:
    """Extract successful facts; failures raise :class:`VideoRequestFactsError`."""

    if isinstance(result, VideoRequestFactsFailure):
        raise VideoRequestFactsError(result)
    return result


def planning_durations(facts: VideoRequestFacts) -> list[int]:
    """Available script-planning duration tiers: narrowed tiers; endpoint-fixed durations borrow :data:`ENDPOINT_FIXED_PLANNING_DURATIONS`.

    Successful facts have empty ``allowed_durations`` only for endpoint-fixed durations (empty intersections fail), so this function always returns
    nonempty tiers for successful facts. Storyboard script planning and reference-route segmentation/prompt authoring all read here, sharing narrowed
    results with preflight/execution. Borrowing is internal to planning; UI and Agent payloads still receive empty sets plus endpoint-fixed flags.
    """

    if facts.allowed_durations:
        return list(facts.allowed_durations)
    if facts.duration_endpoint_fixed:
        return list(ENDPOINT_FIXED_PLANNING_DURATIONS)
    return []


def reference_migration_durations(
    with_references: VideoRequestFacts | VideoRequestFactsFailure,
    without_references: VideoRequestFacts | VideoRequestFactsFailure,
) -> list[int] | None:
    """Full duration set for reference-route draft structural adoption: union of r2v/i2v bucket declarations; None if either bucket cannot resolve.

    Both online entries, content-confirmation conversion and Web content confirmation, share this, yielding identical tier members regardless of migration write order.
    """

    if isinstance(with_references, VideoRequestFacts) and isinstance(without_references, VideoRequestFacts):
        return sorted(set(with_references.supported_durations) | set(without_references.supported_durations))
    return None


def audio_switch_conflict(facts: VideoRequestFacts) -> VideoRequestFactsFailure | None:
    """Return the shared issue code when users disable audio but bucket models always output sound."""

    if not facts.requested_generate_audio and facts.has_audio_track and not facts.audio_switch_controllable:
        return VideoRequestFactsFailure(
            "video_audio_switch_not_supported",
            (("provider", facts.provider_id), ("model", facts.model_id)),
        )
    return None


def video_audio_model_facts(audio_track: str | None, *, voice_consistency: str) -> tuple[bool, bool]:
    """Return model-level ``(has_audio_track, audio_switch_controllable)`` facts from bucket output-track shapes.

    Track shapes branch by execution subpath, so read the actual bucket (models like Kling v3-omni with controllable image-to-video but no reference-video
    switch); see :meth:`ConfigResolver.video_audio_track`. Absent signals (``None``: unregistered models or custom-provider models without
    audio-track shape coverage) remain absent without narrowing.
    """

    if audio_track is None:
        return voice_consistency != "none", True
    return audio_track != "always_off", audio_track == "controllable"


async def evaluate_video_request_facts(
    project: dict,
    *,
    route: VideoRoute,
    generation_type: VideoGenerationType,
    identity: VideoRequestIdentity,
    resolver: ConfigResolver,
    resolution_override: ResolutionOverride | None = None,
    builtin_endpoint: str | None = None,
) -> VideoRequestFacts | VideoRequestFactsFailure:
    """Evaluate execution facts for one video request; unresolved values return typed failures without raising or falling back.

    ``resolution_override`` only previews unsaved resolutions (see :class:`ResolutionOverride`); absent overrides resolve
    persisted project configuration. ``builtin_endpoint`` supplies the endpoint chosen during execution backend construction;
    evaluate capabilities, audio tracks and audio pricing against it, otherwise read current configuration.
    """

    codes = _ROUTE_FAILURE_CODES[route]
    if isinstance(identity, ExecutionVideoIdentity):
        provider_id, model_id = identity.provider_id, identity.model_id
    else:
        try:
            selected = await resolver.resolve_video_backend(project, None, generation_type=generation_type)
        except (VideoBucketCapabilityError, ModelNotInCatalogError) as exc:
            return VideoRequestFactsFailure(exc.code, tuple(exc.params.items()))
        except (ValueError, SQLAlchemyError):
            return VideoRequestFactsFailure(codes["unavailable"], (("capability", generation_type),))
        provider_id, model_id = selected.provider_id, selected.model_id

    try:
        caps = await resolver.video_capabilities_for_model(
            provider_id, model_id, project, generation_type=generation_type, builtin_endpoint=builtin_endpoint
        )
    except VideoSupportedDurationsError as exc:
        return VideoRequestFactsFailure(codes[exc.kind], (("provider", exc.provider_id), ("model", exc.model_id)))
    except (ValueError, SQLAlchemyError):
        return VideoRequestFactsFailure(
            codes["unavailable"],
            (("capability", generation_type), ("provider", provider_id), ("model", model_id)),
        )
    capability_identity = str(caps["provider_id"]), str(caps["model"])
    if capability_identity != (provider_id, model_id):
        return VideoRequestFactsFailure(
            codes["unavailable"],
            (("capability", generation_type), ("provider", provider_id), ("model", model_id)),
        )
    identity_params = (("provider", provider_id), ("model", model_id))

    raw_durations = caps.get("supported_durations") or []
    if any(isinstance(d, bool) or not isinstance(d, int) or d <= 0 for d in raw_durations):
        return VideoRequestFactsFailure(codes["invalid"], identity_params)
    supported = tuple(sorted(set(raw_durations)))
    endpoint_fixed = bool(caps.get("duration_endpoint_fixed"))
    if not supported and not endpoint_fixed:
        return VideoRequestFactsFailure(codes["missing"], identity_params)

    if resolution_override is not None and resolution_override.resolution is not None:
        resolution = resolution_override.resolution
    else:
        # Auto overrides mean no persisted project tier: resolve with empty projects; custom providers still use model-default tiers.
        saved_settings = project if resolution_override is None else {}
        try:
            resolution = await resolver.resolve_resolution(saved_settings, provider_id, model_id)
        except (ValueError, SQLAlchemyError):
            return VideoRequestFactsFailure(codes["unavailable"], (("capability", generation_type), *identity_params))

    # Reference-image constraints follow buckets: available references determine unit buckets; r2v carries references, i2v does not.
    uses_reference_images = generation_type == "r2v"
    try:
        constraints = await resolver.video_duration_constraints(provider_id, model_id)
    except SQLAlchemyError:
        return VideoRequestFactsFailure(codes["unavailable"], (("capability", generation_type), *identity_params))
    allowed = tuple(
        apply_duration_constraints(
            list(supported),
            constraints,
            resolution=resolution,
            uses_reference_images=uses_reference_images,
        )
    )
    if supported and not allowed:
        return VideoRequestFactsFailure(
            codes["incompatible"],
            (*identity_params, ("resolution", resolution), ("capability", generation_type)),
        )
    # If both constraints exclude the same duration, report references: changing resolution cannot restore it.
    reference_allowed = (
        apply_duration_constraints(list(supported), constraints, uses_reference_images=True)
        if uses_reference_images
        else list(supported)
    )
    excluded: tuple[tuple[int, DurationExclusionReason], ...] = tuple(
        (d, "reference" if d not in reference_allowed else "resolution") for d in supported if d not in allowed
    )

    voice_consistency: VoiceConsistency = caps.get("voice_consistency") or "soft"
    try:
        audio_track = await resolver.video_audio_track(
            provider_id, model_id, generation_type=generation_type, builtin_endpoint=builtin_endpoint
        )
    except SQLAlchemyError:
        return VideoRequestFactsFailure(codes["unavailable"], (("capability", generation_type), *identity_params))
    has_audio_track, audio_switch_controllable = video_audio_model_facts(
        audio_track, voice_consistency=voice_consistency
    )
    max_reference_images = caps.get("max_reference_images")
    return VideoRequestFacts(
        route=route,
        generation_type=generation_type,
        provider_id=provider_id,
        model_id=model_id,
        resolution=resolution,
        supported_durations=supported,
        allowed_durations=allowed,
        excluded_durations=excluded,
        duration_endpoint_fixed=endpoint_fixed,
        requested_generate_audio=bool(caps.get("requested_generate_audio")),
        generate_audio=bool(caps.get("generate_audio")) and has_audio_track,
        has_audio_track=has_audio_track,
        audio_switch_controllable=audio_switch_controllable,
        max_reference_images=_optional_count(max_reference_images),
        text_to_video=bool(caps.get("text_to_video", True)),
        first_frame=bool(caps.get("first_frame")),
        voice_consistency=voice_consistency,
        max_reference_audio_count=_optional_count(caps.get("max_reference_audio_count", 0)),
        reference_audio_per_image=bool(caps.get("reference_audio_per_image")),
    )


def _optional_count(value: object) -> int | None:
    """Count limits from capability dicts: ``None`` means uncataloged; otherwise read integers (unreadable means 0, refused)."""
    if value is None:
        return None
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0
