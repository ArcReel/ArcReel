"""GenerationContext: a single consolidation point for provider-resolution results of a generation task (see ``docs/adr/0049``).

``resolve_generation_context`` resolves every declared lane and constructs backends within one
ConfigResolver session, returning an immutable :class:`GenerationContext` (MediaGenerator + lane result
value objects). Each lane follows a fixed order: resolve ProviderModel → construct the backend through
``assemble_backend`` (``docs/adr/0039``) → look up resolution and video request facts by actual identity (``docs/adr/0086``).

Lookup identity = (canonical registry provider_id, actual backend model). Providers cannot drift at the
construction seam, but family-alias providers (e.g. ark-agent-plan reusing the Ark backend) report a
family name in ``backend.name``, not a registry key, so it cannot serve as the lookup key. Model is the
only actual drift axis (the loader silently falls back when a custom provider's target model is disabled),
so use the backend's actual ``.model``. Lane results expose both ``provider_model`` (canonical registry
identity) and ``backend_name`` / ``backend_model`` (actual backend-reported identity).

This module also holds backend-instance caching: caching belongs to server execution (``docs/adr/0039``,
"caching stays with the caller"). Provider-configuration routes invalidate it through ``invalidate_backend_cache()``."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from lib.backends.audio_backends.base import AudioCapability, VoiceOption
from lib.backends.backend_assembly import assemble_backend, callable_model_key
from lib.backends.backend_assembly.specs import builtin_model_endpoint
from lib.backends.gemini_shared import get_shared_rate_limiter
from lib.backends.image_capabilities import backend_reference_image_limit
from lib.backends.resume_guard import builtin_endpoint_guard
from lib.config.resolver import (
    ConfigResolver,
    VideoGenerationType,
    video_bucket_for_generation_mode,
)
from lib.custom_provider import is_custom_provider
from lib.custom_provider.backends import CustomVideoBackend
from lib.db.base import DEFAULT_USER_ID
from lib.generation.media_generator import MediaGenerator
from lib.generation.video_request_facts import (
    ExecutionVideoIdentity,
    VideoRequestFacts,
    VideoRequestFactsError,
    VideoRequestFactsFailure,
    VideoRoute,
    evaluate_video_request_facts,
)
from lib.project.project_manager import get_project_manager

if TYPE_CHECKING:
    from lib.config.resolver import ProviderModel


rate_limiter = get_shared_rate_limiter()

_CacheKey = tuple[str, str, str | None, str | None, str | None]


class _BackendCache:
    """Backend-instance cache: reuse by (media_type, provider_name, model, task-type bucket, built-in call endpoint), avoiding API-client reconstruction per task.

    Include the bucket because it participates in construction: custom-provider defaults are slotted by
    bucket, so the same (media_type, provider, model=None) loads different-model backends for t2i and i2i.
    Built-in models' attached endpoints also participate; including them prevents reuse of an instance
    constructed with an old endpoint after changes (settings edits or official catalog synchronization).

    Centralize cache lookup/construction/writeback/invalidation here, hiding two concurrency disciplines
    without expanding the interface:

    - **Generation invariant**: ``invalidate()`` increments the generation; capture it before waiting for the
      lock, not after acquiring it. Write back only if unchanged after construction. If changed, discard the
      instance after use, whether invalidation happened during locked construction or while this request
      queued before the boundary and acquired the lock afterward. Do not cache it over new configuration;
      this task still completes using its enqueue-time configuration snapshot.
    - **Per-key single-flight**: serialize concurrent misses for one key with a per-key lock, constructing
      once and sharing the instance. Avoid unowned extra SDK clients (the library has no backend-closing protocol)."""

    def __init__(self) -> None:
        self._entries: dict[_CacheKey, Any] = {}
        self._locks: dict[_CacheKey, asyncio.Lock] = {}
        self._generation = 0

    async def get_or_create(self, key: _CacheKey, factory: Callable[[], Awaitable[Any]]) -> Any:
        if key in self._entries:
            return self._entries[key]
        # Capture generation before waiting for the lock: requests queued before invalidation must compare their old generation
        # against the post-invalidation current one even if they acquire the lock afterward, avoiding new-cache pollution by old-resolver instances.
        generation = self._generation
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self._entries:
                return self._entries[key]
            backend = await factory()
            if self._generation == generation:
                self._entries[key] = backend
            return backend

    def invalidate(self) -> None:
        self._generation += 1
        self._entries.clear()


_backend_cache = _BackendCache()


def invalidate_backend_cache() -> None:
    """Clear the backend-instance cache after provider configuration changes."""
    _backend_cache.invalidate()


def reset_backend_cache_for_tests() -> None:
    """For test isolation: clear backend instances and per-key locks without changing production behavior.

    ``invalidate_backend_cache()`` deliberately clears entries only, not locks (production uses one event
    loop for the process lifetime). Tests switch loops between cases; contended ``asyncio.Lock`` objects
    bind to a closed old loop, so clear them too."""
    _backend_cache.invalidate()
    _backend_cache._locks.clear()


async def _get_or_create_backend(
    media_type: str,
    provider_name: str,
    provider_settings: dict,
    resolver: ConfigResolver,
    default_model: str | None,
    generation_type: str | None = None,
    *,
    submitted_job: bool = False,
) -> tuple[Any, str | None]:
    """Build the key and supply a factory closure, delegating cache discipline to :class:`_BackendCache`;
    return the backend and the built-in call endpoint used to construct it.

    Before cache lookup, determine this call's model and check its lifecycle as of today
    (``callable_model_key``, for both built-in and custom providers). A cached instance may predate its
    shutdown date; after that date, neither the retired model nor a retired default may be called via cache.

    When ``submitted_job`` is true (resuming an already-submitted provider task; see :class:`VideoLaneRequest`),
    skip retirement checks before lookup and during construction. It is not part of the cache key: resumed
    instances share the cache with new calls, which still run ``callable_model_key`` checks before retrieval.

    For built-in models, read the attached endpoint only once here: use it for construction, cache keys,
    video-resume guards (:func:`_video_endpoint_guard`), and request-fact evaluation. Even if users change
    the endpoint during subsequent awaits, facts and guards remain consistent with the actual submitting
    instance. For custom providers it is None."""
    effective_model = await callable_model_key(
        provider_id=provider_name,
        media_type=media_type,
        model_id=provider_settings.get("model") or default_model or None,
        resolver=resolver,
        generation_type=generation_type,
        submitted_job=submitted_job,
    )

    endpoint = (
        None
        if is_custom_provider(provider_name)
        else builtin_model_endpoint(provider_name, media_type, effective_model)
    )

    async def _factory() -> Any:
        return await assemble_backend(
            provider_id=provider_name,
            media_type=media_type,
            model_id=effective_model,
            resolver=resolver,
            rate_limiter=rate_limiter,
            generation_type=generation_type,
            submitted_job=submitted_job,
            builtin_endpoint=endpoint,
        )

    backend = await _backend_cache.get_or_create(
        (media_type, provider_name, effective_model, generation_type, endpoint), _factory
    )
    return backend, endpoint


async def _get_or_create_video_backend(
    provider_name: str,
    provider_settings: dict,
    resolver: ConfigResolver,
    *,
    default_video_model: str | None = None,
    submitted_job: bool = False,
) -> tuple[Any, str | None]:
    """Get or create a cached VideoBackend instance, with its construction-time built-in call endpoint.

    provider_name may use old forms (gemini/seedance/grok) or new forms (gemini-aistudio/gemini-vertex).
    Load provider configuration on demand through the resolver.
    default_video_model: global default video model, used when provider_settings has no model.
    submitted_job: resume an already-submitted provider task; see :class:`VideoLaneRequest`."""
    backend, endpoint = await _get_or_create_backend(
        "video", provider_name, provider_settings, resolver, default_video_model, submitted_job=submitted_job
    )
    return backend, endpoint


async def _get_or_create_image_backend(
    provider_name: str,
    provider_settings: dict,
    resolver: ConfigResolver,
    *,
    default_image_model: str | None = None,
    generation_type: Literal["t2i", "i2i"] | None = None,
):
    """Get or create a cached ImageBackend instance.

    generation_type is this call's task-type bucket: custom-provider defaults are slotted by bucket.
    Pass it through construction to ``load_custom_backend`` and include it in the cache key
    (t2i and i2i never hit each other's entries)."""
    backend, _ = await _get_or_create_backend(
        "image", provider_name, provider_settings, resolver, default_image_model, generation_type
    )
    return backend


async def _get_or_create_audio_backend(
    provider_name: str,
    provider_settings: dict,
    resolver: ConfigResolver,
    *,
    default_audio_model: str | None = None,
):
    """Get or create a cached AudioBackend instance. Audio has no media-specific exception: custom and simple-family providers share the construction seam."""
    backend, _ = await _get_or_create_backend("audio", provider_name, provider_settings, resolver, default_audio_model)
    return backend


async def _audio_backend_for(provider_id: str, model_id: str, resolver: ConfigResolver):
    """Construct or retrieve the specified cached audio backend. Propagate construction errors unchanged for unconfigured providers or unavailable models."""
    async with resolver.session() as r:
        return await _get_or_create_audio_backend(provider_id, {}, r, default_audio_model=model_id)


@dataclass(frozen=True)
class AudioBackendProfile:
    """Capabilities and voice catalog declared by the specified audio backend (in backend-provided voice order)."""

    capabilities: frozenset[AudioCapability]
    voices: tuple[VoiceOption, ...]


async def audio_backend_profile(provider_id: str, model_id: str) -> AudioBackendProfile:
    """Specified audio backend capabilities and voices, letting settings determine whether speech rate applies and list voices for the endpoint this model uses."""
    from lib.db import async_session_factory

    backend = await _audio_backend_for(provider_id, model_id, ConfigResolver(async_session_factory))
    return AudioBackendProfile(capabilities=frozenset(backend.capabilities), voices=tuple(backend.list_voices()))


async def audio_backend_voice_ids(provider_id: str, model_id: str, resolver: ConfigResolver) -> tuple[str, ...]:
    """Voice IDs for the specified audio backend, in backend-provided order, for model-matching voice prefills on project creation."""
    return tuple(v.id for v in (await _audio_backend_for(provider_id, model_id, resolver)).list_voices())


@dataclass(frozen=True)
class ImageLaneRequest:
    """Declare that this task needs an image lane. ``generation_type`` selects the t2i / i2i default slot (``docs/adr/0001``)."""

    generation_type: Literal["t2i", "i2i"] = "t2i"


@dataclass(frozen=True)
class VideoLaneRequest:
    """Declare that this task needs a video lane.

    ``generation_type`` selects the i2v / r2v task-type bucket (``docs/adr/0054``): image-to-video / grids → i2v;
    reference-to-video routes by actual resolved unit reference images: with images → r2v, without images
    → i2v fallback (declared after executor determination; see ``lib.script.reference_video.units``). With
    both fields None, leave the bucket unspecified, use three-level resolution, and skip capability gates
    for payload-draining paths that make no capability promise.

    Declaring ``route`` attaches that route's execution-side video request facts (``docs/adr/0086``); read
    capabilities only through them. Without an explicit bucket, derive it from project generation mode for
    identity resolution and evaluation. Lanes without a route (checkpoint-based resume draining) do not evaluate capabilities.

    ``submitted_job`` declares resolution only for resuming a task already submitted to the provider:
    poll an existing task without starting generation, so identity resolution and backend construction skip
    retirement checks, allowing results retrieval after shutdown for tasks submitted beforehand. The payload
    must pin the submitted execution identity. Only resume entry points (``server.services.tasks.resume_executor``)
    set it; it is always false for new submissions. Tasks crossing the shutdown date after enqueue but
    before provider submission still report retirement normally."""

    generation_type: VideoGenerationType | None = None
    route: VideoRoute | None = None
    submitted_job: bool = False


@dataclass(frozen=True)
class AudioLaneRequest:
    """Declare that this task needs an audio lane (narration voiceover)."""


@dataclass(frozen=True)
class ImageLaneResult:
    """Image lane resolution result.

    ``provider_model`` is canonical registry identity; ``backend_name`` / ``backend_model`` are actual
    constructed-backend identities. If a disabled custom-provider target model triggers fallback,
    ``backend_model`` may differ from ``provider_model.model_id``. ``resolution`` None means omit the
    SDK argument at call time (``docs/adr/0019``). ``max_reference_images`` is the truncation limit from
    the effective reference-image cap (evaluated on both entry and channel sides; see
    ``lib.backends.image_capabilities.backend_reference_image_limit``); ``None`` means no truncation.
    Orchestration truncates the reference sequence before rendering “图N” numbering. Also do not truncate
    when references are unsupported (limit 0 or no image-to-image support): pass the originals through
    for execution-time validation to reject."""

    provider_model: ProviderModel
    backend_name: str
    backend_model: str
    resolution: str | None
    max_reference_images: int | None


@dataclass(frozen=True)
class VideoLaneResult:
    """Video lane resolution result.

    Capability facts exist only in ``request_facts``: execution-side video request facts evaluated by the
    actual constructed backend identity. Resolution failure is a typed failure without fallback; None
    means the lane request declared no route. ``resolution`` is request resolution by actual identity;
    None omits that argument at call time (``docs/adr/0019``). Other identity fields follow :class:`ImageLaneResult`."""

    provider_model: ProviderModel
    backend_name: str
    backend_model: str
    resolution: str | None
    request_facts: VideoRequestFacts | VideoRequestFactsFailure | None = None
    # Resume without a route preserves user audio intent; with a route, read only video request facts.
    requested_generate_audio_fallback: bool = True
    # Resume protocol identifier: custom providers use the endpoint key (including path overrides; see
    # CustomVideoBackend.endpoint_guard); built-in providers use the model's attached call endpoint (lib.backends.resume_guard).
    # Resume compares it with the frozen execution-checkpoint identifier; see server.services.tasks.resume_executor.
    endpoint: str | None = None

    @property
    def requested_generate_audio(self) -> bool:
        facts = self.request_facts
        if isinstance(facts, VideoRequestFactsFailure):
            raise VideoRequestFactsError(facts)
        return facts.requested_generate_audio if facts is not None else self.requested_generate_audio_fallback

    @property
    def is_silent(self) -> bool:
        """Whether this episode has no audible sound: the model produces none (class C), or episode audio is disabled; both paths use the same rule.

        Voice consistency comes from video request facts, defaulting to "soft" on unresolved facts (only a
        signal establishes true silence; see ``lib.config.resolver.derive_voice_consistency``). This criterion
        also omits voice-characteristic descriptions: although prompt text rather than audio payload, they
        describe audible timbre, so injecting them into silent output wastes model quota on unused constraints.
        Dialogue ignores this bit: send dialogue text even for silent video, since providers may use it for lip-sync.
        For reference-to-video, see ``lib.script.reference_video.voice_settings.VoiceRenderSettings.is_silent``."""
        facts = self.request_facts
        if isinstance(facts, VideoRequestFactsFailure):
            raise VideoRequestFactsError(facts)
        voice_consistency = facts.voice_consistency if isinstance(facts, VideoRequestFacts) else "soft"
        return voice_consistency == "none" or not self.requested_generate_audio


@dataclass(frozen=True)
class AudioLaneResult:
    """Audio lane resolution result. Deliver the voice catalog and backend resolution within the same session.

    ``voices`` is a value snapshot of the backend's voice enumeration, not a backend instance. Voice-list
    endpoints answer from it and synthesis tasks validate requested voices from it; neither needs further backend access."""

    provider_model: ProviderModel
    backend_name: str
    backend_model: str
    voices: tuple[VoiceOption, ...]


def _lane_not_declared(lane: str, request_hint: str) -> RuntimeError:
    return RuntimeError(
        f"{lane} lane 未声明：调用 resolve_generation_context 时传入 {request_hint} 才能访问该 lane 的解析产物"
    )


@dataclass(frozen=True)
class GenerationContext:
    """All results of one resolution: MediaGenerator plus value objects for every declared lane.

    A None lane field means undeclared. Access through its same-named property immediately raises
    RuntimeError (fail loud, non-Optional return type). Tests can directly assemble fake contexts with this dataclass."""

    generator: MediaGenerator
    image_lane: ImageLaneResult | None = None
    video_lane: VideoLaneResult | None = None
    audio_lane: AudioLaneResult | None = None

    @property
    def image(self) -> ImageLaneResult:
        if self.image_lane is None:
            raise _lane_not_declared("image", "image=ImageLaneRequest(...)")
        return self.image_lane

    @property
    def video(self) -> VideoLaneResult:
        if self.video_lane is None:
            raise _lane_not_declared("video", "video=VideoLaneRequest()")
        return self.video_lane

    @property
    def audio(self) -> AudioLaneResult:
        if self.audio_lane is None:
            raise _lane_not_declared("audio", "audio=AudioLaneRequest()")
        return self.audio_lane


def _video_endpoint_guard(backend: object, builtin_endpoint: str | None) -> str | None:
    """Protocol identifier frozen at submission and compared on resume (see lib.backends.resume_guard), from the endpoint used to construct this backend.

    For built-in providers, use the attached construction-time call endpoint; for custom providers, use the
    wrapper's endpoint key and path override. Dispatch explicitly by type instead of getattr probing:
    renaming an attribute would make probing silently fail without a signal."""
    if builtin_endpoint is not None:
        return builtin_endpoint_guard(builtin_endpoint)
    return backend.endpoint_guard if isinstance(backend, CustomVideoBackend) else None


async def resolve_generation_context(
    project_name: str,
    payload: dict | None,
    *,
    project: dict,
    project_path: Path | None = None,
    user_id: str = DEFAULT_USER_ID,
    image: ImageLaneRequest | None = None,
    video: VideoLaneRequest | None = None,
    audio: AudioLaneRequest | None = None,
) -> GenerationContext:
    """Resolve every declared lane, construct backends, and assemble MediaGenerator within one ConfigResolver session.

    Passing a lane declares it; None skips it, so tasks incur configuration requirements and construction
    costs only for used lanes. Any declared lane's resolution/construction error propagates unchanged and
    fails the entire call: no partial results or cross-provider fallback. Video-request-fact evaluation failures
    reach the executor as failure objects for stage-specific handling. ``project`` is the caller's loaded
    project snapshot; transactions already holding a path may pass ``project_path`` to avoid nested default
    thread-pool occupancy while synchronous transactions resolve current configuration. This function does not read projects.

    Video bucket selection follows ``VideoLaneRequest.generation_type``: None derives from project
    generation mode (see ``lib.config.resolver.caps_generation_mode``), fixed at creation for the whole
    project. Thus two-dimensional derived values such as voice consistency need no episode number.
    An explicit value resolves the specified bucket (reference-to-video callers routing per video unit
    supply their own determination)."""
    from lib.db import async_session_factory

    resolved_project_path = (
        project_path
        if project_path is not None
        else await asyncio.to_thread(get_project_manager().get_project_path, project_name)
    )
    resolver = ConfigResolver(async_session_factory)

    image_result: ImageLaneResult | None = None
    image_provider_model: ProviderModel | None = None
    image_resolution: str | None = None
    video_result: VideoLaneResult | None = None
    audio_result: AudioLaneResult | None = None
    image_backend: Any = None
    video_backend: Any = None
    audio_backend: Any = None

    async with resolver.session() as r:
        if image is not None:
            resolved = await r.resolve_image_backend(project, payload, generation_type=image.generation_type)
            image_backend = await _get_or_create_image_backend(
                resolved.provider_id,
                {},
                r,
                default_image_model=resolved.model_id or None,
                generation_type=image.generation_type,
            )
            image_provider_model = resolved
            image_resolution = await r.resolve_resolution(project, resolved.provider_id, image_backend.model)

        if video is not None:
            generation_type = video.generation_type
            if generation_type is None and video.route is not None:
                generation_type = video_bucket_for_generation_mode(project.get("generation_mode"))
            resolved = await r.resolve_video_backend(
                project, payload, generation_type=generation_type, submitted_job=video.submitted_job
            )
            video_backend, builtin_endpoint = await _get_or_create_video_backend(
                resolved.provider_id,
                {},
                r,
                default_video_model=resolved.model_id or None,
                submitted_job=video.submitted_job,
            )
            actual_model = video_backend.model
            request_facts: VideoRequestFacts | VideoRequestFactsFailure | None = None
            if video.route is not None:
                assert generation_type is not None
                # Include this task's bucket: per-path capability bits such as audio-track shape branch by execution subpath;
                # reference-to-video shots falling back to i2v evaluate with the i2v bucket.
                request_facts = await evaluate_video_request_facts(
                    project,
                    route=video.route,
                    generation_type=generation_type,
                    identity=ExecutionVideoIdentity(resolved.provider_id, actual_model),
                    resolver=r,
                    builtin_endpoint=builtin_endpoint,
                )
            requested_generate_audio_fallback = True
            if isinstance(request_facts, VideoRequestFacts):
                resolution = request_facts.resolution
            elif isinstance(request_facts, VideoRequestFactsFailure):
                resolution = None
            else:
                resolution = await r.resolve_resolution(project, resolved.provider_id, actual_model)
                requested_generate_audio_fallback = await r.video_generate_audio_for_project(project)
            video_result = VideoLaneResult(
                provider_model=resolved,
                backend_name=video_backend.name,
                backend_model=actual_model,
                resolution=resolution,
                request_facts=request_facts,
                requested_generate_audio_fallback=requested_generate_audio_fallback,
                endpoint=_video_endpoint_guard(video_backend, builtin_endpoint),
            )

        if audio is not None:
            resolved = await r.resolve_audio_backend(project, payload)
            audio_backend = await _get_or_create_audio_backend(
                resolved.provider_id,
                {},
                r,
                default_audio_model=resolved.model_id or None,
            )
            audio_result = AudioLaneResult(
                provider_model=resolved,
                backend_name=audio_backend.name,
                backend_model=audio_backend.model,
                voices=tuple(audio_backend.list_voices()),
            )

    if image_provider_model is not None:
        # The reference-image limit may require a provider model-catalog query (e.g. OpenRouter); keep it outside the resolver session
        # so an external request does not occupy a database connection.
        image_result = ImageLaneResult(
            provider_model=image_provider_model,
            backend_name=image_backend.name,
            backend_model=image_backend.model,
            resolution=image_resolution,
            max_reference_images=(
                await backend_reference_image_limit(image_backend, provider_id=image_provider_model.provider_id)
            ).clamp_bound(),
        )

    generator = MediaGenerator(
        resolved_project_path,
        rate_limiter=rate_limiter,
        image_backend=image_backend,
        video_backend=video_backend,
        audio_backend=audio_backend,
        config_resolver=resolver,
        user_id=user_id,
        image_provider_id=image_result.provider_model.provider_id if image_result else None,
        video_provider_id=video_result.provider_model.provider_id if video_result else None,
        audio_provider_id=audio_result.provider_model.provider_id if audio_result else None,
    )
    return GenerationContext(
        generator=generator,
        image_lane=image_result,
        video_lane=video_result,
        audio_lane=audio_result,
    )
