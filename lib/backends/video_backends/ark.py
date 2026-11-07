"""ArkVideoBackend: Volcengine Ark video-generation backend."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import httpx

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.video_backend_contract import (
    ReferenceAudioMode,
    ResumeExpiredError,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.ark_shared import ark_base_url, create_ark_client
from lib.backends.backend_runtime import (
    ProviderJobIdPersistenceMixin,
    download_video,
    poll_with_retry,
    reference_audio_to_data_uri,
    should_retry_download,
)
from lib.backends.provider_assets import (
    REFERENCE_IMAGE_ROLE,
    InferenceCredentialRef,
    ProviderInputImageRejectedError,
    is_input_image_rejection,
)
from lib.backends.providers import PROVIDER_ARK
from lib.backends.transport_layer import TransportLayer
from lib.backends.video_backends.metering import video_usage
from lib.billing.metering import Usage
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

# Reference-audio data URI MIME: official wav / mp3 formats require `data:audio/<格式>;base64,<内容>`
# with lowercase formats. Upload validation already narrows to the same formats; infer from extensions, rejecting unknowns without guessing.
# Deliberately keep a separate table from dashscope: official examples here use audio/mp3, while dashscope uses standard
# audio/mpeg. Vendor-specific acceptance differs; sharing would send unverified MIME values to one.
_REFERENCE_AUDIO_MIME_TYPES = {".wav": "audio/wav", ".mp3": "audio/mp3"}

# Official Seedance video-token estimate (Model Pricing, video generation):
#   (input video duration + output video duration) × output width × height × frame rate / 1024
# Output frame rate/pixels per resolution × ratio come from Create Video Generation Task API: 24 fps; 2.5/2.0 differ only at 480p
# 16:9 / 9:16; 2.5 lacks 4k. Unsent resolution defaults to 720p. Settle using provider-reported completion_tokens.
_SEEDANCE_FPS = 24
_SEEDANCE_DEFAULT_RESOLUTION = "720p"
_SEEDANCE_2_PIXELS: dict[tuple[str, str], tuple[int, int]] = {
    ("480p", "16:9"): (864, 496),
    ("480p", "4:3"): (752, 560),
    ("480p", "1:1"): (640, 640),
    ("480p", "3:4"): (560, 752),
    ("480p", "9:16"): (496, 864),
    ("480p", "21:9"): (992, 432),
    ("720p", "16:9"): (1280, 720),
    ("720p", "4:3"): (1112, 834),
    ("720p", "1:1"): (960, 960),
    ("720p", "3:4"): (834, 1112),
    ("720p", "9:16"): (720, 1280),
    ("720p", "21:9"): (1470, 630),
    ("1080p", "16:9"): (1920, 1080),
    ("1080p", "4:3"): (1664, 1248),
    ("1080p", "1:1"): (1440, 1440),
    ("1080p", "3:4"): (1248, 1664),
    ("1080p", "9:16"): (1080, 1920),
    ("1080p", "21:9"): (2206, 946),
    ("4k", "16:9"): (3840, 2160),
    ("4k", "4:3"): (3326, 2494),
    ("4k", "1:1"): (2880, 2880),
    ("4k", "3:4"): (2494, 3326),
    ("4k", "9:16"): (2160, 3840),
    ("4k", "21:9"): (4398, 1886),
}
_SEEDANCE_2_5_PIXELS: dict[tuple[str, str], tuple[int, int]] = {
    **{key: size for key, size in _SEEDANCE_2_PIXELS.items() if key[0] != "4k"},
    ("480p", "16:9"): (854, 480),
    ("480p", "9:16"): (480, 854),
}

# Requested service tier → price-table tier: Ark ``default`` means standard.
_CATALOG_SERVICE_TIERS = {"default": "standard"}


@dataclass(frozen=True)
class SeedanceEndpoint:
    """Request structure/metering for one built-in Seedance endpoint (channel facts independent of model names).

    Seedance 1, 2, and 2.5 share task creation/polling interfaces but differ in bodies, metering, and asset-reference contracts, each forming an endpoint.
    Model entries record accepted inputs within each endpoint (``lib.backends.video_capabilities``).
    """

    #: Built-in endpoint names shared with custom providers (``lib.custom_provider.endpoints.ENDPOINT_REGISTRY`` keys).
    key: str
    #: Whether bodies send ``service_tier``: only 1.x accepts it; 2.0 rejects with 400 for references, 2.5 follows the same-generation omission.
    sends_service_tier: bool
    #: Resolution × ratio → output-pixel table for video-token estimates; ``None`` means no token estimation (1.x bills per call).
    token_pixels: Mapping[tuple[str, str], tuple[int, int]] | None
    #: Whether reference slots accept ``asset://`` references: official Seedance Portrait Asset Guide contracts cover only 2.0 / 2.5.
    reference_image_asset: bool
    #: Whether present first frames force upstream ratio adaptation (2.5); see ``VideoCapabilities.first_frame_ratio_adaptive_only``.
    first_frame_ratio_adaptive_only: bool


SEEDANCE_1 = SeedanceEndpoint(
    key="ark-seedance-1",
    sends_service_tier=True,
    token_pixels=None,
    reference_image_asset=False,
    first_frame_ratio_adaptive_only=False,
)
SEEDANCE_2 = SeedanceEndpoint(
    key="ark-seedance-2",
    sends_service_tier=False,
    token_pixels=_SEEDANCE_2_PIXELS,
    reference_image_asset=True,
    first_frame_ratio_adaptive_only=False,
)
SEEDANCE_2_5 = SeedanceEndpoint(
    key="ark-seedance-2-5",
    sends_service_tier=False,
    token_pixels=_SEEDANCE_2_5_PIXELS,
    reference_image_asset=True,
    first_frame_ratio_adaptive_only=True,
)


def _retry_ark_download(exc: Exception) -> bool:
    """Shared artifact-download predicates ⊕ Ark-specific successful-but-unready artifacts (400 video_not_ready)."""
    if (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code == 400
        and "video_not_ready" in str(exc.response.text)
    ):
        return True
    return should_retry_download(exc)


def _safe_create_params_for_log(create_params: dict[str, Any]) -> dict[str, Any]:
    """Safe request log views: collapse content arrays into counts by item type, without expanding embedded materials.

    Images/audio in content are base64 data URIs. ``format_kwargs_for_log`` truncates long strings only,
    without recognizing this structure, so direct use would log each asset base64 prefix at info level (audio may include
    mp3 ID3 metadata). dashscope / vidu / minimax each have equivalent safe views, all aligned with
    CodeQL clear-text-logging.

    Prompts are user text, not assets; retain existing ``format_kwargs_for_log`` truncation rules,
    preserving the most commonly useful item for generation-effect diagnostics.
    """
    view = {key: value for key, value in create_params.items() if key != "content"}
    content = create_params.get("content")
    if isinstance(content, list):
        counts: dict[str, int] = {}
        for item in content:
            kind = item.get("type", "unknown") if isinstance(item, dict) else "unknown"
            counts[kind] = counts.get(kind, 0) + 1
        view["content"] = "<" + ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items())) + ">"
        prompt = next(
            (item.get("text") for item in content if isinstance(item, dict) and item.get("type") == "text"), None
        )
        if isinstance(prompt, str):
            view["prompt"] = prompt
    return view


def _reference_asset_uris(request: VideoGenerationRequest) -> list[str | None]:
    """Asset references matching reference images in length/order; all None when absent from requests."""
    refs = request.reference_images or []
    uris = request.reference_image_asset_uris
    if uris is None:
        return [None] * len(refs)
    if len(uris) != len(refs):
        raise ValueError("reference_image_asset_uris must align with reference_images")
    return list(uris)


def _input_image_rejection(
    request: VideoGenerationRequest, provider_code: object, provider_reason: object
) -> ProviderInputImageRejectedError | None:
    """Structured errors when Ark rejects input images for moderation; None for other errors.

    Ark errors lack rejected-image positions, so identify images only when requests contain exactly one;
    never guess in other cases.
    """
    if not is_input_image_rejection(provider_code):
        return None
    refs = request.reference_images or []
    frames = [image for image in (request.start_image, request.end_image) if image]
    reference_index = 0 if len(refs) == 1 and not frames else None
    asset_uris = _reference_asset_uris(request)
    return ProviderInputImageRejectedError(
        provider_code=str(provider_code),
        provider_reason=provider_reason if isinstance(provider_reason, str) and provider_reason else None,
        reference_index=reference_index,
        sent_as_asset=reference_index is not None and asset_uris[reference_index] is not None,
    )


def _task_failure(task: Any, request: VideoGenerationRequest) -> str | None:
    """Polled terminal failures: raise structured errors for rejected input images; return other failure reasons, or None without failure."""
    if task.status not in ("failed", "expired"):
        return None
    error = getattr(task, "error", None)
    rejection = _input_image_rejection(request, getattr(error, "code", None), getattr(error, "message", None))
    if rejection is not None:
        raise rejection
    return f"Ark 视频生成失败(status={task.status}): {error or 'Unknown error'}"


class ArkVideoBackend(ProviderJobIdPersistenceMixin):
    """Ark (Volcengine Ark) video backend: shared Seedance-generation implementation, with endpoint-specific request/metering differences from :attr:`endpoint`.

    Three endpoint subclasses (:class:`Seedance1VideoBackend`, etc.) drive registry, capabilities, and metering, never model names.
    """

    endpoint: ClassVar[SeedanceEndpoint]

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
        inference_credential: InferenceCredentialRef | None = None,
    ):
        self._client = create_ark_client(api_key=api_key, base_url=base_url, transport_layer=transport_layer)
        self._transport_layer = transport_layer
        # Replay submission hosts on resume by constructing clients with the same credentials (SDK binds base_url at construction without public host switching).
        self._api_key = api_key
        # Actual request host uses create_ark_client normalization; persist with job_id on submission.
        self._base_url = ark_base_url(base_url)
        if not model:
            raise ValueError("ArkVideoBackend 需要指定模型")
        self._model = model
        self._inference_credential = inference_credential

    @property
    def name(self) -> str:
        return PROVIDER_ARK

    @property
    def inference_credential(self) -> InferenceCredentialRef | None:
        """Actual inference credential used by this instance; asset libraries select bound provider assets accordingly. None if assembly supplies none."""
        return self._inference_credential

    @property
    def provider_asset_roles(self) -> frozenset[str]:
        """Slots accepting ``asset://`` references instead of image content, declared per endpoint (:attr:`SeedanceEndpoint.reference_image_asset`).

        First/last frames lack official contracts and remain undeclared; undeclared slots send image content normally.
        """
        return frozenset({REFERENCE_IMAGE_ROLE}) if self.endpoint.reference_image_asset else frozenset()

    @property
    def model(self) -> str:
        return self._model

    @classmethod
    def estimate_video_tokens(cls, *, resolution: str | None, ratio: str, seconds: int) -> float | None:
        """Estimate video tokens using official formulas; ``None`` for nontoken-metered endpoints, ``adaptive`` ratios, or tiers absent from pixel tables.

        Built-in channels never send input videos; input-video duration is always zero.
        """
        pixels = cls.endpoint.token_pixels
        if pixels is None:
            return None
        size = pixels.get(((resolution or _SEEDANCE_DEFAULT_RESOLUTION).lower(), ratio))
        if size is None:
            return None
        width, height = size
        return seconds * width * height * _SEEDANCE_FPS / 1024

    @classmethod
    def metered_usage(
        cls,
        model: str,
        request: VideoGenerationRequest,
        result: VideoGenerationResult | None = None,
        *,
        entry: CatalogEntry | None = None,
    ) -> Usage:
        """This request's measurements (:mod:`lib.backends.video_backends.metering`), endpoint-defined independently of model names/entries.

        Settle tokens from provider-reported ``completion_tokens`` (``result.usage_tokens``), estimating through
        :meth:`estimate_video_tokens`. Report service tiers only for endpoints sending them, using request-construction predicates.
        ``model`` and ``entry`` are standard metering-contract inputs, unused here.
        """
        del model, entry
        tokens = (
            result.usage_tokens
            if result is not None
            else cls.estimate_video_tokens(
                resolution=request.resolution, ratio=request.aspect_ratio, seconds=request.duration_seconds
            )
        )
        tier = request.service_tier
        return video_usage(
            request,
            result,
            seconds=request.duration_seconds,
            resolution=request.resolution,
            audio=request.generate_audio,
            video_tokens=tokens,
            dimensions=(
                {"service_tier": _CATALOG_SERVICE_TIERS.get(tier, tier)} if cls.endpoint.sends_service_tier else None
            ),
        )

    @classmethod
    def video_capabilities_for_model(cls, model: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
        """Ark video-channel declarations (channel facts): pure computation without SDK clients/api_key, independent of model names.

        All models use the same construction to send first/last frames (``role: first_frame / last_frame``), references
        (``role: reference_image``), and audio references (``role: reference_audio``), without count limits. Accepted
        inputs/counts are model facts stored in entries, jointly evaluated with declarations (``lib.backends.video_capabilities``,
        docs/adr/0104). Endpoint declares first-frame ratio constraints: Seedance 2.5 must let upstream adapt to present first frames;
        fixed ratio would conflict with their dimensions. ``model`` and ``entry`` are standard capability-contract inputs, unused here.

        All families use default ``VideoCapabilities`` audio behavior (toggleable): ``_build_payload`` unconditionally includes
        ``generate_audio`` in bodies, accepted by every Seedance tier, so no branched declarations needed.
        """
        del model, entry
        return VideoCapabilities(
            text_to_video=True,
            first_frame=True,
            last_frame=True,
            max_reference_images=None,
            reference_audio_mode=ReferenceAudioMode.DIRECT,
            max_reference_audio_count=None,
            first_frame_ratio_adaptive_only=cls.endpoint.first_frame_ratio_adaptive_only,
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Generate videos with separate creation/polling retry scopes, preventing transient errors from recreating tasks."""
        try:
            provider_task_id = await self._create_task(request)
        except Exception as exc:
            body = getattr(exc, "body", None)
            reason = body.get("message") if isinstance(body, dict) else getattr(exc, "message", None)
            rejection = _input_image_rejection(request, getattr(exc, "code", None), reason)
            if rejection is not None:
                raise rejection from exc
            raise
        # Persist actual request host too: resume replays it, otherwise changed base_url would query old tasks on new hosts.
        await self._persist_provider_job_id(request, provider_task_id, provider=PROVIDER_ARK, endpoint=self._base_url)
        return await self._poll_until_done(provider_task_id, request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Resume submitted Ark tasks with polling/download only.

        Missing/expired Ark tasks typically raise SDK 404 exceptions or return status='expired';
        _poll_until_done recognizes the latter, while this method catches the former as ResumeExpiredError.
        """
        try:
            return await self._poll_until_done(job_id, request)
        except Exception as exc:
            if _is_ark_not_found(exc):
                raise ResumeExpiredError(job_id=job_id, provider=PROVIDER_ARK) from exc
            raise

    @with_retry_async()
    async def _create_task(self, request: VideoGenerationRequest) -> str:
        """Create Ark video tasks with retry protection."""
        # 1. Build content list
        content: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]

        # Ark requires top-level `role` in every image_url item
        # (first_frame / last_frame / reference_image), otherwise 400 InvalidParameter.
        # Existence checks and image reads/encoding are blocking I/O: offload together to threads.
        content.extend(await asyncio.to_thread(self._build_image_content, request))

        if request.reference_audio_files:
            # Audio and image items share content arrays, independently numbered by type: prompt “音频N”
            # means the Nth type="audio_url" item (official Quick Start prompt rules). Therefore preserve
            # original reference_audio_files order without skipping any clips; skipping shifts orchestration-authored
            # references and assigns character A's voice to B. gather returns input order, so
            # concurrency preserves numbering. Whole-audio base64 encoding is blocking I/O: offload each clip to threads.
            audio_data_uris = await asyncio.gather(
                *[
                    asyncio.to_thread(
                        reference_audio_to_data_uri,
                        Path(audio_path),
                        model=self._model,
                        mime_types=_REFERENCE_AUDIO_MIME_TYPES,
                    )
                    for audio_path in request.reference_audio_files
                ]
            )
            content.extend(
                {
                    "type": "audio_url",
                    "audio_url": {"url": data_uri},
                    "role": "reference_audio",
                }
                for data_uri in audio_data_uris
            )

        # 2. Build API params
        # Ratio first: independent SDK ratio follows aspect_ratio directly; resolution controls clarity only,
        # orthogonal to ratio (SDK derives pixels from ratio×resolution), without squeezing ratios into pixel size or size bugs.
        create_params = {
            "model": self._model,
            "content": content,
            "ratio": request.aspect_ratio,
            "duration": request.duration_seconds,
            "generate_audio": request.generate_audio,
            "watermark": False,
        }
        if request.resolution is not None:
            create_params["resolution"] = request.resolution
        # Only Seedance 1.x endpoints accept service_tier; see SeedanceEndpoint.sends_service_tier.
        if self.endpoint.sends_service_tier:
            create_params["service_tier"] = request.service_tier
        if request.seed is not None:
            create_params["seed"] = request.seed

        # 3. Create task (sync SDK call, run in executor)
        logger.info(
            "调用 %s 视频 SDK kwargs=%s", self.name, format_kwargs_for_log(_safe_create_params_for_log(create_params))
        )
        create_result = await asyncio.to_thread(
            self._client.content_generation.tasks.create,
            **create_params,
        )
        logger.info("Ark 任务已创建: %s", create_result.id)
        return create_result.id

    @staticmethod
    async def _download_video_with_retry(video_url: str, output_path) -> None:
        """Retry video downloads separately, preventing retrieval failures from regenerating videos and wasting quota.

        After task succeeded, Ark video URLs may still be unready, returning 400 video_not_ready
        rather than ordinary artifact 403/404; add this case to shared predicates.
        """
        await download_video(video_url, output_path, label="Ark", retry_if=_retry_ark_download)

    def _build_image_content(self, request: VideoGenerationRequest) -> list[dict[str, Any]]:
        """First / last / reference images → image_url items (sync local reads; callers run in threads)."""
        from lib.backends.image_backends.base import image_to_base64_data_uri

        content: list[dict[str, Any]] = []
        if request.start_image:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_to_base64_data_uri(request.start_image)},
                    "role": "first_frame",
                }
            )

        if request.end_image:
            end_path = Path(request.end_image)
            if not end_path.is_file():
                # Never silently skip missing last frames: tasks would still submit/bill, leaving users unaware why output lacks
                # required endings and cannot connect to storyboards.
                raise VideoCapabilityError(
                    "video_end_image_unreadable", model=self._model, name=end_path.name or str(end_path)
                )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_to_base64_data_uri(request.end_image)},
                    "role": "last_frame",
                }
            )

        if request.reference_images:
            asset_uris = _reference_asset_uris(request)
            if any(asset_uris) and REFERENCE_IMAGE_ROLE not in self.provider_asset_roles:
                raise ValueError(f"model {self._model} does not accept provider asset references")
            # References sent as asset references require no local reads; validate only other images.
            missing = [
                p
                for r, uri in zip(request.reference_images, asset_uris, strict=True)
                if uri is None and not (p := Path(r)).is_file()
            ]
            if missing:
                # Like last frames: missing reference images still produce billed outputs with drifting characters/scenes.
                raise VideoCapabilityError(
                    "video_reference_images_unreadable",
                    model=self._model,
                    names=", ".join(p.name or str(p) for p in missing),
                )
            # Asset references replace image content at the same position, without insertion/deletion/reordering: prompt “图片N” still points to the Nth reference item.
            content.extend(
                {
                    "type": "image_url",
                    "image_url": {"url": uri if uri is not None else image_to_base64_data_uri(Path(ref_path))},
                    "role": "reference_image",
                }
                for ref_path, uri in zip(request.reference_images, asset_uris, strict=True)
            )
        return content

    @contextmanager
    def _polling_client(self, request: VideoGenerationRequest) -> Generator[Any]:
        """Polling SDK client: for resume with changed hosts, construct a submission-host client using the same credentials.

        Task IDs are queryable only on creation endpoints; changed base_url would query old tasks on new hosts, returning 404
        misclassified as expiry by ``resume_video``. Submission ``submitted_base_url`` stays None, reusing instance clients.
        New clients own independent connection pools, closed after polling.
        """
        submitted = request.submitted_base_url
        if not submitted or ark_base_url(submitted) == self._base_url:
            yield self._client
            return
        client = create_ark_client(api_key=self._api_key, base_url=submitted, transport_layer=self._transport_layer)
        try:
            yield client
        finally:
            client.close()

    async def _poll_until_done(self, task_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Poll task state until completion, retrying transient errors for individual queries only."""
        with self._polling_client(request) as client:
            result = await poll_with_retry(
                poll_fn=lambda: asyncio.to_thread(client.content_generation.tasks.get, task_id=task_id),
                is_done=lambda r: r.status == "succeeded",
                is_failed=lambda r: _task_failure(r, request),
                max_wait=request.poll_timeout_seconds,
                label="Ark",
                on_progress=lambda r, elapsed: logger.info(
                    "Ark 视频生成中... 状态: %s, 已等待 %d 秒", r.status, int(elapsed)
                ),
            )

        # Download video
        video_url = result.content.video_url
        await self._download_video_with_retry(video_url, request.output_path)

        # Extract result metadata
        seed = getattr(result, "seed", None)
        usage_tokens = None
        if hasattr(result, "usage") and result.usage:
            usage_tokens = getattr(result.usage, "completion_tokens", None)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_ARK,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=video_url,
            seed=seed,
            usage_tokens=usage_tokens,
            task_id=task_id,
            generate_audio=request.generate_audio,
        )


class Seedance1VideoBackend(ArkVideoBackend):
    """``ark-seedance-1``: Seedance 1.x request structure/metering."""

    endpoint = SEEDANCE_1


class Seedance2VideoBackend(ArkVideoBackend):
    """``ark-seedance-2``: Seedance 2.0 request structure/metering."""

    endpoint = SEEDANCE_2


class Seedance25VideoBackend(ArkVideoBackend):
    """``ark-seedance-2-5``: Seedance 2.5 request structure/metering."""

    endpoint = SEEDANCE_2_5


#: Built-in Seedance endpoint name → implementing channel, in declaration order (1, 2, 2.5).
SEEDANCE_BACKENDS: Mapping[str, type[ArkVideoBackend]] = {
    backend.endpoint.key: backend for backend in (Seedance1VideoBackend, Seedance2VideoBackend, Seedance25VideoBackend)
}


def _is_ark_not_found(exc: BaseException) -> bool:
    """Recognize nonexistent / expired Ark tasks.

    Match stable official ``task_not_found`` codes exactly; remove broad ``"not found"`` substring fallback,
    preventing business errors such as reference image not found from masquerading as provider task expiry.
    Retain ``expired``: Ark ``_poll_until_done`` converts status in (failed, expired)
    into ``RuntimeError("Ark 任务失败 ... status=expired")``, requiring that substring to restore ResumeExpiredError.
    """
    status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if status_code == 404:
        return True
    msg = str(exc).lower()
    return "task_not_found" in msg or "tasknotfound" in msg or "expired" in msg
