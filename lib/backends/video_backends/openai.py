"""OpenAIVideoBackend: OpenAI Sora video-generation backend."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from openai import AsyncOpenAI

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.aspect_size import VIDEO_TIER_SHORT_EDGE, parse_aspect_ratio, resolution_to_short_edge
from arcreel_market_core.video_backend_contract import (
    IMAGE_MIME_TYPES,
    TERMINAL_PROVIDER_STATUSES,
    ProviderJobStatus,
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
    normalize_provider_status,
)
from lib.backends.backend_runtime import (
    ProviderJobIdPersistenceMixin,
    faststart_video_artifact,
    poll_with_retry,
    with_artifact_retry,
)
from lib.backends.openai_shared import OPENAI_RETRYABLE_ERRORS, create_openai_client
from lib.backends.providers import PROVIDER_OPENAI
from lib.backends.transport_layer import TransportLayer
from lib.backends.video_backends.metering import video_usage
from lib.backends.video_backends.name_family import (
    EntryBoundCapabilities,
)
from lib.billing.metering import Usage
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

# Sora input_reference accepts one file; first frames/references share the slot, read by capabilities and presubmission checks.
_INPUT_REFERENCE_SLOTS = 1


# Endpoint accepts two size families; entries record model resolutions.
# SDK VideoSize Literal lags (720/1024 only, missing 1080); use official model pages.
# Legal sizes exclude 1024x1792 / 1792x1024 (4:7 violates ratio priority; exact 1080p tiers cover the use).
# No arbitrary WxH: snap to legal sizes by ratio first; resolution tiers select 720p vs 1080p subsets, with clarity secondary.
_SORA_SIZES_720P: tuple[str, ...] = ("720x1280", "1280x720")
_SORA_SIZES_1080P: tuple[str, ...] = ("1080x1920", "1920x1080")
# Backward-compatible union export for external/tests referencing all legal sizes.
_SORA_LEGAL_SIZES: tuple[str, ...] = _SORA_SIZES_720P + _SORA_SIZES_1080P
_SORA_1080P_MIN_SHORT = 1080


def _video_status(video: object) -> ProviderJobStatus:
    """SDK Video object → canonical status.

    This endpoint serves built-in Sora and custom-provider openai-video protocols: official Sora returns only
    ``queued`` / ``in_progress`` / ``completed`` / ``failed``, while OpenAI-compatible proxy gateways forwarding
    other models pass through vendor statuses such as ``succeeded``; normalize all through shared logic.
    """
    return normalize_provider_status(getattr(video, "status", None))


def _video_error_message(video: object) -> str:
    """SDK Video object → provider failure-reason text; unknown if unavailable.

    This text enters ``task.error_message`` unchanged, the entire reason visible in task panels, so explicitly extract fields rather than
    interpolate objects: ``Video.error`` is a model with ``code`` / ``message``; direct interpolation exposes class/field names
    to users. ``None`` (common when gateways return status without errors) gives failures without reasons.
    Also recognize proxy raw dicts/strings; unknown for all unrecognized shapes.
    """
    err = getattr(video, "error", None)
    if err is None:
        return "unknown"
    if isinstance(err, str):
        return err.strip() or "unknown"
    if isinstance(err, dict):
        candidates = (err.get("message"), err.get("code"))
    else:
        candidates = (getattr(err, "message", None), getattr(err, "code", None))
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
        # Numeric error codes (often raw HTTP codes from gateways) are valid reasons too; never discard for being nonstrings.
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    # Unknown shapes without explainable reasons use unknown; object repr in task panels provides no useful explanation.
    return "unknown"


def _resolve_size(model: str, resolution: str | None, aspect_ratio: str, *, entry: CatalogEntry | None = None) -> str:
    """Snap to entry resolutions and legal endpoint sizes, prioritizing ratio; unlisted models use endpoint constraints only."""
    aw, ah = parse_aspect_ratio(aspect_ratio)
    target = aw / ah
    short = resolution_to_short_edge(resolution, tier_map=VIDEO_TIER_SHORT_EDGE)
    # Get legal short-edge tiers under entry resolution caps, then choose nearest (higher on ties), avoiding
    # unnecessarily downgrading custom resolutions such as short=1000 to 720p (floor would; nearest does not).
    from lib.backends.video_capabilities import entry_video_facts
    from lib.catalog import query as catalog

    facts = entry_video_facts(entry or catalog.find_entry(model, media_type="video"))
    ceiling = max(
        (resolution_to_short_edge(r, tier_map=VIDEO_TIER_SHORT_EDGE) for r in facts.resolutions), default=1080
    )
    supported_shorts = [720, _SORA_1080P_MIN_SHORT] if ceiling >= 1080 else [720]
    achieved_short = min(supported_shorts, key=lambda s: (abs(s - short), -s))
    legal = _SORA_SIZES_1080P if achieved_short == _SORA_1080P_MIN_SHORT else _SORA_SIZES_720P
    if short > achieved_short:
        # Cap requests above entry/endpoint maxima, warning that clarity yields.
        logger.warning(
            "OpenAI video: model=%s 无法满足分辨率请求 %s（短边 %d），输出封顶到 %dp 档（清晰度让位比例）",
            model,
            resolution,
            short,
            achieved_short,
        )

    def _score(size: str) -> tuple[float, int]:
        w, h = (int(x) for x in size.split("x"))
        return abs(w / h - target), -(w * h)  # Prefer smaller ratio differences, then higher pixel counts for ties.

    chosen = min(legal, key=_score)
    # Exact 9:16 / 16:9; other ratios such as 1:1 / 21:9 lack Sora sizes and necessarily deviate after snapping.
    # Warn independently of upstream protocol to avoid silent wrong-ratio video (image paths likewise warn for out-of-range ratios).
    cw, ch = (int(x) for x in chosen.split("x"))
    if abs(cw / ch - target) > 0.01:
        logger.warning(
            "OpenAI video: aspect_ratio=%s 无精确 sora 档，吸附到 %s（比例偏差，输出非项目设定比例）",
            aspect_ratio,
            chosen,
        )
    # Postcondition: return only sizes in the complete Sora legal set, preventing future snapping changes from silently producing invalid size.
    assert chosen in _SORA_LEGAL_SIZES, f"_resolve_size produced illegal sora size: {chosen}"
    return chosen


class OpenAIVideoBackend(EntryBoundCapabilities, ProviderJobIdPersistenceMixin):
    """OpenAI Sora video-generation backend."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
    ):
        self._client = create_openai_client(api_key=api_key, base_url=base_url, transport_layer=transport_layer)
        if not model:
            raise ValueError("OpenAIVideoBackend 需要指定模型")
        self._model = model

    @property
    def name(self) -> str:
        return PROVIDER_OPENAI

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
        """Sora channel declarations (channel facts): pure computation without SDK clients or api_key.

        input_reference accepts one first-frame file; references share that slot, limiting the channel to one image. No last frames/audio references.
        Entries record model input acceptance, jointly evaluated with declarations (``lib.backends.video_capabilities``, docs/adr/0104).
        Instance properties delegate here, keeping backends the single authority.

        Output always has audio: Sora includes audio and ``generate`` kwargs contain no audio-switch field,
        so user disable intent cannot be sent.
        """
        del model, entry
        return VideoCapabilities(max_reference_images=_INPUT_REFERENCE_SLOTS, audio_track=VideoAudioMode.ALWAYS_ON)

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model, self._catalog_entry)

    @property
    def _request_base_url(self) -> str:
        """Actual request host from the SDK client public property, stripping SDK-added trailing slashes."""
        return str(self._client.base_url).rstrip("/")

    def _client_for(self, request: VideoGenerationRequest) -> AsyncOpenAI:
        """Polling/retrieval client: derive one bound to submission hosts when resuming across configuration changes.

        Task IDs are queryable only on their creation endpoints; changed base_url would query old tasks on new hosts, yielding 404
        misclassified as expiry by ``resume_video``. Clients derived through ``with_options`` reuse credentials, timeout/retry
        settings, and connection pools. Submission paths keep ``submitted_base_url`` None and use instance clients.
        """
        submitted = request.submitted_base_url
        if not submitted or submitted.rstrip("/") == self._request_base_url:
            return self._client
        return self._client.with_options(base_url=submitted)

    @staticmethod
    def metered_usage(
        model: str,
        request: VideoGenerationRequest,
        result: VideoGenerationResult | None = None,
        *,
        entry: CatalogEntry | None = None,
    ) -> Usage:
        """This request's measurements (:mod:`lib.backends.video_backends.metering`).

        Resolution uses sent ``size`` short-edge tiers (``1024x1792`` → 1024p); ``size`` uses the same model-corresponding
        entry (``entry``) as request construction. No audio switch exists, so omit the audio dimension.
        """
        width, _, height = _resolve_size(model, request.resolution, request.aspect_ratio, entry=entry).partition("x")
        return video_usage(
            request,
            result,
            seconds=request.duration_seconds,
            resolution=f"{min(int(width), int(height))}p",
            audio=None,
        )

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        # Sora bodies lack last-frame/reference-audio fields; user-relaxed capabilities still cannot send them. Reject before billing, never silently discard.
        if request.end_image:
            raise VideoCapabilityError("video_last_frame_unsupported", provider=self.name, model=self._model)
        if request.reference_audio_files:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._model)
        # First frames/references exceeding the shared slot would be dropped or rejected upstream; reject before billing.
        input_count = bool(request.start_image) + len(request.reference_images or [])
        if input_count > _INPUT_REFERENCE_SLOTS:
            raise VideoCapabilityError(
                "video_reference_images_exceeded",
                provider=self.name,
                model=self._model,
                limit=_INPUT_REFERENCE_SLOTS,
                count=input_count,
            )
        kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "seconds": str(request.duration_seconds),
        }
        # Always send legal size to lock ratio (fixed Sora tiers; omission selects upstream default ratios).
        kwargs["size"] = _resolve_size(
            self._model,
            request.resolution,
            request.aspect_ratio,
            entry=self._catalog_entry,
        )

        # Collect start_image + reference_images: existence checks/reads are blocking I/O, offload together to threads.
        refs = await asyncio.to_thread(_encode_input_references, request, self._model)
        if refs:
            (kwargs["input_reference"],) = refs

        logger.info("OpenAI 视频生成开始: model=%s, seconds=%s", self._model, kwargs["seconds"])
        logger.info("调用 %s 视频 SDK kwargs=%s", self.name, format_kwargs_for_log(kwargs))

        video = await self._create_video(**kwargs)
        # Persist job_id immediately after successful submission; persistence failures raise, then finally mark_failed.
        # Nonworker paths (grid/direct/tests) have request.task_id=None; centralized persistence skips them.
        # Persist actual request host too: resume replays it, otherwise changed base_url would query old tasks on new hosts.
        await self._persist_provider_job_id(
            request, video.id, provider=PROVIDER_OPENAI, endpoint=self._request_base_url
        )
        final = await self._poll_until_complete(self._client, video.id, request.poll_timeout_seconds)

        # In generate, expired means provider anomalies / expired input parameters;
        # raise RuntimeError so worker mark_failed runs without [resume_expired] prefix.
        if _video_status(final) is ProviderJobStatus.EXPIRED:
            raise RuntimeError(f"OpenAI Sora job expired during generate: {final.id}")

        return await self._download_and_build_result(self._client, final, request, kwargs)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Resume submitted OpenAI jobs: poll/download only, never videos.create; replay submission hosts for polling/retrieval."""
        client = self._client_for(request)
        try:
            final = await self._poll_until_complete(client, job_id, request.poll_timeout_seconds)
        except Exception as exc:
            if _is_openai_not_found(exc):
                raise ResumeExpiredError(job_id=job_id, provider=PROVIDER_OPENAI) from exc
            raise

        # In resume, expired means provider forgot tasks / input assets expired; classify as
        # [resume_expired] so workers prefix errors and stop restart self-healing attempts.
        if _video_status(final) is ProviderJobStatus.EXPIRED:
            raise ResumeExpiredError(
                job_id=job_id,
                provider=PROVIDER_OPENAI,
                message=f"OpenAI Sora job expired: {final.id}",
            )

        return await self._download_and_build_result(client, final, request, {"seconds": str(request.duration_seconds)})

    async def _download_and_build_result(
        self, client: AsyncOpenAI, final, request: VideoGenerationRequest, kwargs: dict
    ) -> VideoGenerationResult:
        content = await self._download_content_with_retry(client, final.id)

        def _write():
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            request.output_path.write_bytes(content.content)

        await run_noninterruptible_sync(_write)
        await faststart_video_artifact(request.output_path)

        logger.info("OpenAI 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_OPENAI,
            model=self._model,
            duration_seconds=int(
                final.seconds if final.seconds is not None else kwargs.get("seconds") or request.duration_seconds
            ),
            task_id=final.id,
        )

    @with_retry_async(retryable_errors=OPENAI_RETRYABLE_ERRORS)
    async def _create_video(self, **kwargs):
        """Create video tasks only, with retries; _poll_until_complete manages polling separately."""
        return await self._client.videos.create(**kwargs)

    async def _poll_until_complete(self, client: AsyncOpenAI, video_id: str, poll_timeout_seconds: int):
        """Poll until statuses normalize into terminal states.

        Do not reuse SDK client.videos.poll: it recognizes only in_progress/queued/completed/failed;
        OpenAI-compatible gateways returning nonstandard statuses such as NOT_START would exit prematurely, downloading unready tasks.
        """
        # is_done is pure: success/failure/expiry all count as terminal, returning from poll.
        # Callers (generate / resume_video) route results afterward:
        #   - succeeded → download
        #   - failed    → is_failed already raised RuntimeError
        #   - expired   → caller raises RuntimeError / ResumeExpiredError by generate/resume context
        # Key invariant: is_failed never recognizes expired, preserving caller routing.
        return await poll_with_retry(
            poll_fn=lambda: client.videos.retrieve(video_id),
            is_done=lambda v: _video_status(v) in TERMINAL_PROVIDER_STATUSES,
            is_failed=lambda v: (
                f"Sora 视频生成失败: {_video_error_message(v)}"
                if _video_status(v) is ProviderJobStatus.FAILED
                else None
            ),
            max_wait=poll_timeout_seconds,
            retryable_errors=OPENAI_RETRYABLE_ERRORS,
            label="OpenAI",
            on_progress=lambda v, elapsed: logger.info(
                "OpenAI 视频生成中... 状态: %s, 已等待 %d 秒", v.status, int(elapsed)
            ),
        )

    async def _download_content_with_retry(self, client: AsyncOpenAI, video_id: str):
        """Retry content downloads separately, preventing retrieval failures from triggering regeneration.

        SDK retrieval does not raise ``HTTPStatusError``, so use ``OPENAI_RETRYABLE_ERRORS`` predicates;
        shared artifact download budgets still determine termination.
        """
        return await with_artifact_retry(
            lambda: client.videos.download_content(video_id),
            label="OpenAI",
            retry_if=None,
            retryable_errors=OPENAI_RETRYABLE_ERRORS,
        )


def _encode_start_image(image_path: Path) -> tuple[str, bytes, str]:
    mime = IMAGE_MIME_TYPES.get(image_path.suffix.lower(), "image/png")
    return (image_path.name, image_path.read_bytes(), mime)


def _encode_input_references(request: VideoGenerationRequest, model: str) -> list[tuple[str, bytes, str]]:
    """Never silently skip missing first/reference images: output would still bill without using user images."""
    ref_paths: list[Path] = []
    if request.start_image:
        start_path = Path(request.start_image)
        if not start_path.is_file():
            raise VideoCapabilityError("video_start_image_unreadable", model=model, name=start_path.name)
        ref_paths.append(start_path)
    missing = [p.name for p in request.reference_images or [] if not p.is_file()]
    if missing:
        raise VideoCapabilityError("video_reference_images_unreadable", model=model, names=", ".join(missing))
    ref_paths.extend(request.reference_images or [])
    return [_encode_start_image(p) for p in ref_paths]


def _is_openai_not_found(exc: BaseException) -> bool:
    """Recognize OpenAI/Sora nonexistent-job responses (NotFoundError / HTTP 404).

    No ``"not found"`` / ``"expired"`` substring fallback: ``status='expired'`` becomes ``ResumeExpiredError`` directly in ``resume_video``
    after terminal polling. Broad substrings would misclassify business errors such as ``"file not found in storage"``
    as ghost tasks.
    """
    try:
        from openai import NotFoundError
    except ImportError:
        NotFoundError = None

    if NotFoundError is not None and isinstance(exc, NotFoundError):
        return True
    status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return status_code == 404
