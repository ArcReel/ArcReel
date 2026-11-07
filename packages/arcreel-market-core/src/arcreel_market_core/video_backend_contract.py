"""The video invocation channel contract: capability declarations, requests and results, provider job states, and channel protocol.

This module depends only on the standard library and can be imported without cost by any module. It accompanies the endpoint
definition validator into this package: save-time validation and value resolution for declarative definitions need provider job states,
reference-audio modes, and audio-capability checks. Runtime support for submission and polling, retry decisions, downloads to disk,
and persisting provider job IDs lives on the ArcReel application side in ``lib.backends.backend_runtime``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

#: Default for ``VideoGenerationRequest.poll_timeout_seconds`` and the application user-configurable polling timeout.
DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS = 3600

ProviderResponseStage = Literal["submit", "poll", "result"]


class ResumeExpiredError(RuntimeError):
    """The provider job expired or was not found; restart recovery cannot resume it and must use mark_failed.

    Worker finally uses ``isinstance(exc, ResumeExpiredError)`` to prefix error_message
    with ``[resume_expired]`` (agent-facing, exempt from i18n), visible to operations analysis.
    """

    def __init__(self, *, job_id: str, provider: str, message: str = "") -> None:
        self.job_id = job_id
        self.provider = provider
        super().__init__(message or f"resume job {job_id} expired or not found on provider {provider}")


class ResumeEndpointChangedError(RuntimeError):
    """The endpoint used to submit this job differs from the model row's current endpoint; resuming must fail explicitly.

    The endpoint determines the protocol, so changing endpoints changes backends. Polling a job created under the old protocol
    with the new protocol's backend misreads responses and marks a remote job failed while it is still running and incurring charges.
    This applies ADR 0054's requirement to fail explicitly when resuming with a changed identity to the endpoint dimension:
    block only resumes already submitted with a job_id; queued, unsubmitted tasks submit normally through the new endpoint.
    This dimension exists only for custom providers.
    """

    def __init__(self, *, job_id: str, provider: str, submitted_endpoint: str, current_endpoint: str) -> None:
        self.job_id = job_id
        self.provider = provider
        self.submitted_endpoint = submitted_endpoint
        self.current_endpoint = current_endpoint
        super().__init__(
            f"resume job {job_id} was submitted via endpoint {submitted_endpoint} on provider {provider}, "
            f"but the model row now points to {current_endpoint}"
        )


# Image suffix to MIME type mapping (shared by multiple backends)
IMAGE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class ProviderJobStatus(StrEnum):
    """Canonical categories for provider asynchronous job states.

    ``EXPIRED`` is distinct from ``FAILED``: the OpenAI / NewAPI paths use it to raise ``RuntimeError`` or
    ``ResumeExpiredError`` according to generate / resume context. The latter drives the worker's ``[resume_expired]``
    prefix and decision not to retry restart recovery. Folding it into failed silently loses this routing. Endpoints with no
    expiration semantics (such as family C ``/v2/video/generations``) collapse categories above this classification.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


TERMINAL_PROVIDER_STATUSES: frozenset[ProviderJobStatus] = frozenset(
    {ProviderJobStatus.SUCCEEDED, ProviderJobStatus.FAILED, ProviderJobStatus.EXPIRED}
)

# Cross-provider status synonyms (look up after lowercase + strip). OpenAI-compatible proxy gateways forwarding non-native models pass
# underlying provider status strings unchanged. If backends accept only literals in their own documentation, ready jobs appear still running
# and are polled until max_wait: users see timeout failures although the provider has produced and billed the result.
_PROVIDER_STATUS_SYNONYMS: dict[str, ProviderJobStatus] = {
    "completed": ProviderJobStatus.SUCCEEDED,
    "succeeded": ProviderJobStatus.SUCCEEDED,
    "succeed": ProviderJobStatus.SUCCEEDED,
    "success": ProviderJobStatus.SUCCEEDED,
    "failed": ProviderJobStatus.FAILED,
    "fail": ProviderJobStatus.FAILED,
    "error": ProviderJobStatus.FAILED,
    "canceled": ProviderJobStatus.FAILED,
    "cancelled": ProviderJobStatus.FAILED,
    "expired": ProviderJobStatus.EXPIRED,
    "generating": ProviderJobStatus.RUNNING,
    "in_progress": ProviderJobStatus.RUNNING,
    "running": ProviderJobStatus.RUNNING,
    "processing": ProviderJobStatus.RUNNING,
    "queued": ProviderJobStatus.QUEUED,
    "queueing": ProviderJobStatus.QUEUED,
    "preparing": ProviderJobStatus.QUEUED,
    "submitted": ProviderJobStatus.QUEUED,
    "pending": ProviderJobStatus.QUEUED,
    "created": ProviderJobStatus.QUEUED,
}


def normalize_provider_status(raw: object) -> ProviderJobStatus:
    """Map any provider status to a canonical category, ignoring case and surrounding whitespace.

    Treat unregistered status strings as ``RUNNING`` and keep polling: treating unknown strings as terminal causes
    gateways returning nonstandard in-progress statuses (such as ``NOT_START``) to download jobs before they are ready.
    The same applies to nonstrings (missing fields / None).
    """
    if not isinstance(raw, str):
        return ProviderJobStatus.RUNNING
    return _PROVIDER_STATUS_SYNONYMS.get(raw.strip().lower(), ProviderJobStatus.RUNNING)


class VideoCapabilityError(RuntimeError):
    """Video backend capability mismatch (such as duration versus supported_durations).

    Symmetric with ImageCapabilityError: carries no localized text, only a stable code and context params.
    Routes render directly with _t(code, **params); workers persist code and params to task.error_message,
    leaving wording to the read side for rendering under Accept-Language.
    """

    def __init__(self, code: str, **params) -> None:
        self.code = code
        self.params = params
        super().__init__(code)


class ReferenceAudioMode(StrEnum):
    """Transport forms accepted by the backend for reference audio.

    ``DIRECT`` sends audio files directly with generation requests so the model can replicate voice timbre
    (Seedance 2.0 ``role: reference_audio`` content items, or Wan2.7 r2v ``reference_voice`` attached to reference
    material items). ``NONE`` means the backend has no voice-input channel: requests with audio fail hard at
    ``gate_video_request`` rather than silently dropping it.
    """

    NONE = "none"
    DIRECT = "direct"


class VideoAudioMode(StrEnum):
    """Three states for the output audio track and its switch.

    ``CONTROLLABLE`` means requests carry an audio-track switch and the user's on/off intent reaches the provider.
    ``ALWAYS_ON`` means output always has audio and requests have no switch to send (off intent cannot be honored).
    ``ALWAYS_OFF`` means this path produces no audio track and has no switch (on intent cannot be honored).

    This differs from ``reference_audio_mode``, which describes the **input** channel (whether a voice reference can
    be supplied to the model). This enum describes **output** audio tracks. Values correspond one-to-one with frontend
    ``VideoAudioControl`` literals; neither side independently merges them.
    """

    CONTROLLABLE = "controllable"
    ALWAYS_ON = "always_on"
    ALWAYS_OFF = "always_off"


def audio_capability_pair_is_coherent(*, mode: object, count: int | None) -> bool:
    """Post-merge invariant for the two audio dimensions: declaring voice input requires a positive clip-count limit.

    Only one combination is valid individually but meaningless together (``direct`` with limit 0). Sparse custom-provider
    overrides can create it by changing only one dimension: overriding ``reference_audio_mode=direct`` while retaining
    the system-determined 0, or reducing ``max_reference_audio_count`` to 0 while the mode remains system-determined
    ``direct``. Declarative definitions can specify this pair directly. The reverse (``none`` with a positive limit) is not
    a violation: the limit is irrelevant when mode is ``none``, and disabling voice input is legitimate user intent;
    declaring it invalid would reenable a capability explicitly disabled by the user. A clip-count limit of ``None``
    (neither side declares a count, see ``VideoCapabilities``) also complies: no count limit; upstream rejects excess.

    Without correcting this pair, ``gate_video_request`` passes the mode check, then hits limit 0, reporting
    “at most 0 reference-audio clips” instead of “this model does not support reference audio”. Users following this
    guidance reduce character counts, but cannot pass even after reducing to zero clips.

    Three consumers share this check and must not duplicate it: custom-provider writes
    (``server/routers/custom_providers.py``), capability composition
    (``lib/custom_provider/capabilities.py``), and declarative-definition save-time validation
    (``arcreel_market_core/endpoint_definition/validator.py``).
    """
    return mode in {ReferenceAudioMode.NONE, ReferenceAudioMode.NONE.value} or count is None or count > 0


#: Video execution paths (task-type buckets): ``i2v`` covers text-to-video and image-to-video first frames; ``r2v`` is reference-to-video.
#: Shares the vocabulary of ``lib.config.resolver.VideoGenerationType``; layering (config is lowest-level and
#: backend cannot import it in reverse) requires each layer to declare it once. Value equality is guarded by
#: ``tests/unit/lib/backends/video_backends/test_video_backend_capabilities.py``.
VideoRoute = Literal["i2v", "r2v"]


@dataclass
class VideoCapabilities:
    """Declares what a video backend supports.

    ``text_to_video`` states whether pure text-to-video requests with no image assets are available. Default True
    preserves existing backend compatibility; models requiring an image explicitly declare False.

    ``first_frame`` / ``last_frame`` describe first- and last-frame slots on the image-to-video path.
    ``max_reference_images`` describes the reference-to-video path: the maximum accepted ``reference_images`` count.
    ``0`` means reference images are not accepted (a channel declaration means that path is unimplemented); ``None``
    means accepted without a declared count limit. There is no separate boolean: two declarations would drift into
    contradictions such as claiming support with a limit of 0. Channels declare only what they implement and their
    per-request capacity (channel facts); accepted model inputs and counts belong to model entries. Effective capabilities
    combine both constraints (ArcReel ``lib.backends.video_capabilities``, docs/adr/0104), where ``None`` means neither
    side has declared the value, so it is unrecorded. Whether both paths can combine (first frame and reference images
    in one request) varies by backend and is not a uniform contract: some reject the combination (Agnes raises
    ``VideoCapabilityError``), while others silently combine them (v2 proxies, Grok, and Sora share one first-frame/reference
    slot). Callers must verify the specific backend rather than assume uniform behavior.

    ``audio_track`` / ``reference_route_audio_track`` describe **output audio tracks** (presence and switch control),
    the sole source of truth for this dimension. They share the source with request construction: the literal meaning
    is whether the backend places an audio switch in the request body. Declare each execution path once, matching
    how ``first_frame`` / ``max_reference_images`` flatten both paths into one object. ``reference_route_audio_track``
    of None means the reference-to-video path has the same shape as ``audio_track`` (most backends); a non-None value
    means that path's request shape has distinct audio behavior (Kling v3-omni's multi-image subject subpath lacks an
    audio switch in its native schema, so that path is always silent). Default ``CONTROLLABLE`` means no tightening
    without a signal; do not falsely report a broken switch for models with unknown capabilities.
    Resolve through :meth:`audio_track_for_route`, not direct field reads, or every caller must rewrite the
    reference-to-video-first merge rule.

    ``reference_audio_mode`` / ``max_reference_audio_count`` describe reference audio, mirroring reference images:
    a mode other than ``NONE`` accepts the ``reference_audio_files`` request field subject to the clip-count limit.
    ``None`` means no declared clip count. Each backend declares its provider's constraints, not their intersection.

    ``reference_audio_per_image``: whether every audio clip must attach to a specific reference material item
    (such as wan2.7-r2v's ``reference_voice``), rather than a separate voice-input channel (such as Seedance 2.0's
    ``role: reference_audio`` content items). When True, callers must supply
    ``VideoGenerationRequest.reference_audio_targets`` with ``reference_audio_files``, explicitly identifying each clip's
    reference material item. Do not assume both lists naturally share an order: reference audio follows first dialogue
    speaker occurrence, while reference images follow first mention occurrence. Their orders derive independently;
    positional alignment is coincidence.

    ``max_reference_audio_total_seconds``: total-duration limit across reference-audio clips (None = no declared
    aggregate constraint, only the ``max_reference_audio_count`` count limit). Counts do not determine total duration:
    two individually valid clips can together exceed the provider's total limit, requiring a separate declaration.
    Evaluation requires audio metadata; callers probe total duration before
    :func:`lib.backends.video_frame_slots.gate_video_request` and pass it in.

    ``max_prompt_chars``: prompt character limit (None = no declared backend constraint). This limit holds **for this
    model regardless of endpoint**. Some providers impose narrower per-endpoint limits (such as Vidu's reference-to-video
    endpoint); enforce those loudly during backend assembly using the actual endpoint, not in this static declaration
    without endpoint context. Count characters equally for Chinese and English, matching provider documentation.
    The typical over-limit failure is silent truncation rather than an error: the provider charges normally, output
    diverges from intent, and the user has no notice. This is the degradation
    :func:`lib.backends.video_frame_slots.gate_video_request` must prevent before payment.

    ``first_frame_ratio_adaptive_only``: whether this model accepts only the "adaptive" ratio for first-frame
    (image-to-video) tasks. When True, :func:`lib.backends.video_frame_slots.resolve_first_frame_aspect_ratio` rewrites
    ``VideoGenerationRequest.aspect_ratio`` to literal ``"adaptive"`` for generation requests with a first frame.
    Requests without a first frame (pure text-to-video / reference-images-only) and resume paths for already submitted
    jobs are unaffected. This is a provider value meaningful only to backends recognizing it, so only such backends
    declare this flag. Other consumers (such as :func:`arcreel_market_core.aspect_size.parse_aspect_ratio`) cannot parse
    it and fall back to the default ratio for invalid values.

    Other backends express the case where user ratio is inapplicable with a first frame differently: dashscope and vidu
    omit ratio during payload assembly (upstream ignores or rejects it). These three have similar shapes but different
    value strategies (omission versus rewriting to adaptive), so they are not unified under one switch; this flag
    represents the rewrite-to-adaptive branch.

    The user's ratio intent still fully applies to storyboard-image generation: the first-frame image is already made
    at that ratio, so following the first frame equals the selected ratio. Rewriting affects only the video's sent
    request value, not the caller's original ``aspect_ratio`` (used for accounting and version metadata).
    """

    text_to_video: bool = True
    first_frame: bool = True
    last_frame: bool = False
    max_reference_images: int | None = 0
    reference_audio_mode: ReferenceAudioMode = ReferenceAudioMode.NONE
    max_reference_audio_count: int | None = 0
    max_reference_audio_total_seconds: float | None = None
    reference_audio_per_image: bool = False
    max_prompt_chars: int | None = None
    first_frame_ratio_adaptive_only: bool = False
    audio_track: VideoAudioMode = VideoAudioMode.CONTROLLABLE
    reference_route_audio_track: VideoAudioMode | None = None

    def audio_track_for_route(self, route: VideoRoute) -> VideoAudioMode:
        """The effective output audio-track form for this execution path.

        When not separately declared, the reference-to-video path follows ``audio_track``. Both paths normally share
        the same shape; repeating declarations per backend only creates another copy that can drift.
        """
        if route == "r2v" and self.reference_route_audio_track is not None:
            return self.reference_route_audio_track
        return self.audio_track


@dataclass
class VideoGenerationRequest:
    """Common video-generation request. Each backend ignores unsupported fields."""

    prompt: str
    output_path: Path
    aspect_ratio: str = "9:16"
    duration_seconds: int = 5
    resolution: str | None = None
    start_image: Path | None = None
    end_image: Path | None = None  # For first_last mode
    reference_images: list[Path] | None = None  # For multi-reference mode
    # Same length and order as ``reference_images``: a non-None item i sends reference image i using this provider asset reference
    # (such as ``asset://<id>``) instead of image contents, preserving position and prompt image numbering. Only backends declaring
    # asset-reference support for reference-image slots receive non-None items; other backends do not read this field.
    reference_image_asset_uris: list[str | None] | None = None
    # Reference audio (voice replication). List order is the prompt's “音频N” reference contract: orchestration builds identification
    # text in this order and backends send in the same order; neither side may reorder or skip. Character-to-audio bindings are absent from requests
    # because prompt text expresses them; provider APIs have no structured character-to-audio field.
    reference_audio_files: list[Path] | None = None
    # Read only by backends with ``VideoCapabilities.reference_audio_per_image`` True (such as wan2.7-r2v):
    # same length and order as ``reference_audio_files``; item i is this clip's corresponding
    # ``reference_images`` index (0-based). If None, these backends fall back to positional alignment, only for
    # callers not populated by orchestration (such as handwritten tests). Audio and image orders derive independently; positional alignment
    # is not a contract; orchestration (the reference_video rendering pipeline) must supply this explicitly.
    reference_audio_targets: list[int] | None = None
    generate_audio: bool = True
    poll_timeout_seconds: int = DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS

    # Project context (for constructing file-service URLs, etc.)
    project_name: str | None = None

    # Passed from task["task_id"] on worker paths so the backend persists job_id after submit through
    # `ProviderJobIdPersistenceMixin._persist_provider_job_id`.
    # Non-worker paths (grid / direct generation / tests) keep None so the shared point skips persistence.
    task_id: str | None = None

    # MediaGenerator uses this one-way signal to close its compression-retry window. Resumable backends signal
    # after the provider job handle is durable; an opaque submit-and-wait backend must signal before entering a
    # call whose failure cannot prove that the provider rejected the request before accepting a paid job.
    on_provider_resubmit_unsafe: Callable[[], None] | None = None

    # Record provider JSON responses with their stage as diagnostic traces. Non-ledger calls keep None.
    on_provider_response: Callable[[ProviderResponseStage, object], Awaitable[None]] | None = None

    # Protocol identity injected by the custom-provider wrapper (`CustomVideoBackend`) before forwarding to the protocol backend; persist with job_id
    # to `tasks.provider_endpoint` to record this provider job's protocol. Built-in providers have no such dimension and
    # keep None. Resume protocol comparison reads checkpoint endpoint_guard, not this column.
    execution_endpoint: str | None = None

    # Resume-only: actual request domain used to submit this job, replayed by resume_executor from `tasks.submitted_base_url`.
    # Backend polling prefers it over the currently resolved domain: domain is a connection dimension rather than a protocol dimension;
    # after an in-flight configuration change, polling old jobs on the new domain yields not found (404), misread as expired. Always None on submit paths.
    submitted_base_url: str | None = None

    # Capabilities used by the execution gate (tier-aware base plus user overrides), supplied by its caller (MediaGenerator or connection test).
    # Backends deciding sendable inputs from model profiles read this instead of their own profiles, actually sending user-relaxed dimensions for upstream judgment;
    # when None (callers constructing requests directly), fall back to the backend's own profile.
    video_capabilities: VideoCapabilities | None = None

    # Seedance-specific
    service_tier: str = "default"
    seed: int | None = None


@dataclass
class VideoGenerationResult:
    """Common video-generation result."""

    video_path: Path
    provider: str
    model: str
    duration_seconds: int

    video_uri: str | None = None
    seed: int | None = None
    usage_tokens: int | None = None
    task_id: str | None = None
    generate_audio: bool | None = None

    # Facts established only during this generation and archived with the output (such as ComfyUI's actually sent workflow fingerprint). Callers merge
    # them into artifact-version metadata: request-side metadata is finalized before submission and cannot hold values known only after execution.
    # Backends define their own keys; contents must be JSON-serializable because they enter version records unchanged.
    provenance: Mapping[str, Any] | None = None

    # Nonblocking runtime notices with the same ``{"key", "params"}`` shape as task ``result.warnings``: this
    # generation succeeded, but users should know something (such as ComfyUI producing multiple files and only the first being used). Callers
    # merge these into task results for read interfaces to render in the current language; ``key`` must exist in every language table in ``lib/i18n``.
    warnings: tuple[Mapping[str, Any], ...] = ()


class VideoBackend(Protocol):
    """Video-generation backend protocol."""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def video_capabilities(self) -> VideoCapabilities: ...

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult: ...

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Resume an already submitted provider job: poll and download without submitting again (ADR 0007).

        Backends without support raise ``NotImplementedError``; the orphan handler then uses
        ``[resume_unsupported]``. Expired or missing provider jobs raise ``ResumeExpiredError``
        and use ``[resume_expired]``.
        """
        raise NotImplementedError
