"""ViduVideoBackend: Vidu video-generation backend.

Dispatch request fields to four Vidu API endpoints:
- prompt only           → ``/text2video``
- ``start_image``        → ``/img2video``
- ``start_image + end``  → ``/start-end2video``
- ``reference_images``   → ``/reference2video``
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.video_backend_contract import (
    ReferenceAudioMode,
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.backend_runtime import (
    download_video,
    poll_with_retry,
    recording_poll,
    reference_audio_to_data_uri,
    should_retry_submit,
    submit_post,
)
from lib.backends.providers import PROVIDER_VIDU
from lib.backends.transport_layer import TransportLayer
from lib.backends.video_backends.metering import video_usage
from lib.backends.video_backends.name_family import EntryBoundCapabilities
from lib.backends.vidu_shared import (
    VIDU_RETRYABLE_ERRORS,
    assert_vidu_body_size,
    create_vidu_client,
    extract_vidu_url,
    fetch_vidu_task,
    image_to_data_uri,
    is_vidu_done,
    safe_body_for_log,
    vidu_failure_reason,
)
from lib.billing.metering import Usage
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "viduq3-turbo"
# Reference-audio data URIs: official API accepts mp3 only; use data:video/mp3 content type from official reference-to-video examples.
_REFERENCE_AUDIO_MIME_TYPES = {".mp3": "video/mp3"}


@dataclass(frozen=True)
class ViduEndpoint:
    """Fixed audio fields, duration contracts per execution subpath (ADR 0018), and input limits.

    ``max_prompt_chars`` covers text/image/first-last-frame prompts; ``max_reference_prompt_chars`` covers reference-to-video.
    ``max_reference_images`` and ``max_reference_audio`` limit reference-image/audio counts per reference-to-video request;
    zero reference audio means unsupported.
    """

    key: str
    audio: bool
    durations: Mapping[str, tuple[int, ...]]
    max_reference_images: int = 7
    max_prompt_chars: int = 5000
    max_reference_prompt_chars: int = 2000
    max_reference_audio: int = 0

    def prompt_limit(self, path: str) -> int:
        """Prompt limit for subpath ``path``."""
        return self.max_reference_prompt_chars if path == "/reference2video" else self.max_prompt_chars

    @property
    def declared_prompt_limit(self) -> int:
        """Prompt limit declared without request context: reference-only endpoints use reference-to-video limits; others use other subpath limits.

        ``_build_request`` enforces narrower reference-to-video limits on multi-subpath endpoints using the actual selected subpath.
        """
        if set(self.durations) == {"/reference2video"}:
            return self.max_reference_prompt_chars
        return self.max_prompt_chars


VIDU_GENERIC = ViduEndpoint("vidu-video-generic", False, {})
VIDU_Q3_PRO = ViduEndpoint(
    "vidu-video-q3-pro",
    True,
    {"/text2video": tuple(range(1, 17)), "/img2video": tuple(range(1, 17)), "/start-end2video": tuple(range(1, 17))},
)
VIDU_Q3_TURBO = ViduEndpoint(
    "vidu-video-q3-turbo",
    True,
    {
        "/text2video": tuple(range(1, 17)),
        "/img2video": tuple(range(1, 17)),
        "/start-end2video": tuple(range(1, 17)),
        "/reference2video": tuple(range(3, 17)),
    },
)
VIDU_Q3_FAST = ViduEndpoint("vidu-video-q3-fast", True, {"/img2video": tuple(range(1, 17))})
VIDU_Q3_REFERENCE = ViduEndpoint("vidu-video-q3-reference", True, {"/reference2video": tuple(range(3, 17))})
VIDU_Q2 = ViduEndpoint(
    "vidu-video-q2", False, {"/text2video": tuple(range(1, 11)), "/reference2video": tuple(range(1, 11))}
)
VIDU_Q2_PRO = ViduEndpoint(
    "vidu-video-q2-pro",
    False,
    {"/img2video": tuple(range(1, 11)), "/start-end2video": tuple(range(1, 11)), "/reference2video": tuple(range(11))},
)
VIDU_Q2_FAST = ViduEndpoint(
    "vidu-video-q2-fast", False, {"/img2video": tuple(range(1, 11)), "/start-end2video": tuple(range(1, 11))}
)
VIDU_Q1 = ViduEndpoint(
    "vidu-video-q1",
    False,
    {"/text2video": (5,), "/img2video": (5,), "/start-end2video": (5,), "/reference2video": (5,)},
)
VIDU_Q1_CLASSIC = ViduEndpoint("vidu-video-q1-classic", False, {"/img2video": (5,), "/start-end2video": (5,)})
VIDU_2 = ViduEndpoint(
    "vidu-video-2", False, {"/img2video": (4, 8), "/start-end2video": (4, 8), "/reference2video": (4,)}
)
# viduq4-preview: no text or first/last-frame video; reference-to-video accepts 1–15 images, 0–3 audio clips, and 20000-character prompts.
VIDU_Q4 = ViduEndpoint(
    "vidu-video-q4",
    True,
    {"/img2video": tuple(range(3, 17)), "/reference2video": tuple(range(3, 17))},
    max_reference_images=15,
    max_prompt_chars=20000,
    max_reference_prompt_chars=20000,
    max_reference_audio=3,
)


# Whether endpoint accepts aspect_ratio.
_ENDPOINTS_WITH_ASPECT_RATIO = frozenset({"/text2video", "/reference2video"})


class ViduVideoBackend(EntryBoundCapabilities):
    """Vidu video backend dispatching to endpoints by request fields."""

    endpoint = VIDU_Q3_TURBO

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._transport_layer = transport_layer
        self._model = model or DEFAULT_MODEL

    @property
    def name(self) -> str:
        return PROVIDER_VIDU

    @property
    def model(self) -> str:
        return self._model

    @classmethod
    def video_capabilities_for_model(cls, model: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
        """Vidu channel declarations (channel facts): pure computation without client construction.

        Channels route request fields to /text2video, /img2video, /start-end2video, /reference2video; all models can send
        text, first frames, first/last frames, and references. Entries record model input acceptance, jointly evaluated with declarations
        (``lib.backends.video_capabilities``, docs/adr/0104). References and first/last frames are mutually exclusive in Vidu:
        _select_endpoint selects /reference2video for references; that endpoint rejects first/last frames, and _build_request rejects mixed requests.

        Selected endpoints declare audio switches, subpath durations, reference-image/audio counts, and prompt limits (also read by ``_build_request``),
        independently of model names. ``model`` and ``entry`` are standard capability-contract inputs, unused here.
        """
        del model, entry
        endpoint = cls.endpoint
        return VideoCapabilities(
            text_to_video=True,
            first_frame=True,
            last_frame=True,
            max_reference_images=endpoint.max_reference_images,
            max_prompt_chars=endpoint.declared_prompt_limit,
            audio_track=(VideoAudioMode.CONTROLLABLE if endpoint.audio else VideoAudioMode.ALWAYS_OFF),
            reference_audio_mode=(
                ReferenceAudioMode.DIRECT if endpoint.max_reference_audio else ReferenceAudioMode.NONE
            ),
            max_reference_audio_count=endpoint.max_reference_audio,
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model, self._catalog_entry)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        # Vidu cannot resume: polling is entirely inline in generate, without independent resumable polling entry points.
        # Orphan handlers mark [resume_unsupported] from NotImplementedError.
        raise NotImplementedError("ViduVideoBackend 暂不支持 resume_video")

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        endpoint, body = await asyncio.to_thread(self._build_request, request)
        # _build_request normalized duration in body["duration"]; reuse consistently to avoid None crashes/result inconsistencies.
        coerced_duration = int(body["duration"])

        async with create_vidu_client(
            api_key=self._api_key, base_url=self._base_url, transport_layer=self._transport_layer
        ) as client:
            payload = await self._create_task(client, endpoint, body, request)
            task_id = payload["task_id"]
            if request.on_provider_resubmit_unsafe is not None:
                request.on_provider_resubmit_unsafe()
            credits = payload.get("credits")
            logger.info(
                "Vidu 视频任务已创建: endpoint=%s task_id=%s credits=%s model=%s",
                endpoint,
                task_id,
                credits,
                self._model,
            )

            final = await poll_with_retry(
                poll_fn=recording_poll(lambda: fetch_vidu_task(client, task_id), request),
                is_done=is_vidu_done,
                is_failed=vidu_failure_reason,
                max_wait=request.poll_timeout_seconds,
                retryable_errors=VIDU_RETRYABLE_ERRORS,
                label="Vidu",
                on_progress=lambda v, elapsed: logger.info(
                    "Vidu 视频生成中... state=%s elapsed=%ds", v.get("state"), int(elapsed)
                ),
            )
            url = extract_vidu_url(final)
            # credits=0 is valid: never combine with `or`; explicitly fall back to top-level credits only for None.
            final_credits = final.get("credits")
            actual_credits = final_credits if final_credits is not None else credits

        await self._download_video(url, request.output_path)
        logger.info("Vidu 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_VIDU,
            model=self._model,
            duration_seconds=coerced_duration,
            seed=request.seed,
            usage_tokens=int(actual_credits) if actual_credits is not None else None,
            task_id=task_id,
            generate_audio=request.generate_audio if self.endpoint.audio else False,
        )

    # ── request building ────────────────────────────────────────────────

    def _build_request(self, request: VideoGenerationRequest) -> tuple[str, dict]:
        endpoint = self._select_endpoint(request)

        # 0) Reject unsendable inputs before billing, never silently discard: user-relaxed capabilities may pass them through gate.
        profile = self.endpoint
        audio_files = [Path(p) for p in request.reference_audio_files or []]
        if audio_files:
            # Only reference-to-video accepts reference audio (sounds).
            if endpoint != "/reference2video" or not profile.max_reference_audio:
                raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._model)
            if len(audio_files) > profile.max_reference_audio:
                raise VideoCapabilityError(
                    "video_reference_audio_exceeded",
                    provider=self.name,
                    model=self._model,
                    limit=profile.max_reference_audio,
                    count=len(audio_files),
                )
        if endpoint == "/reference2video":
            if request.start_image is not None or request.end_image is not None:
                raise VideoCapabilityError("video_reference_images_with_frames_unsupported", model=self._model)
            count = len(_reference_images(request))
            if count > profile.max_reference_images:
                raise VideoCapabilityError(
                    "video_reference_images_exceeded",
                    model=self._model,
                    limit=profile.max_reference_images,
                    count=count,
                )
        if endpoint == "/text2video" and request.end_image is not None:
            raise VideoCapabilityError("video_end_image_requires_start_image", model=self._model)

        # 1) gate_video_request jointly evaluates model entries/channel facts to decide accepted endpoint inputs (docs/adr/0104).
        #    Do not reject by model here; let upstream decide inputs unlisted in entries.

        # 2) Closest legal duration.
        duration = _coerce_duration(profile, endpoint, request.duration_seconds)

        # 3) Prompt length (declared per endpoint/subpath).
        # Always reject excess, never truncate client-side: truncation produces unintended videos while still charging, without user awareness.
        # Matches gate_video_request: hard failure for a contract violation, never silent degradation. Model-level limits
        # (declared by video_capabilities_for_model) are already gated before billing; here defend narrower endpoint limits
        # at execution because static declarations cannot cover multi-endpoint models using /reference2video.
        prompt = request.prompt or ""
        prompt_max = profile.prompt_limit(endpoint)
        if len(prompt) > prompt_max:
            raise VideoCapabilityError(
                "video_prompt_too_long",
                provider=self.name,
                model=self._model,
                limit=prompt_max,
                count=len(prompt),
            )

        body: dict = {
            "model": self._model,
            "prompt": prompt,
            "duration": duration,
        }

        # 4) Resolution follows catalog entries, passed through unchanged; upstream rejects unsupported tiers (docs/adr/0104).
        if request.resolution:
            body["resolution"] = request.resolution

        # 5) seed
        if request.seed is not None:
            body["seed"] = request.seed

        # 6) Only q3 families accept audio.
        if self.endpoint.audio:
            body["audio"] = bool(request.generate_audio)

        # 7) Only text2video / reference2video accept aspect_ratio.
        if endpoint in _ENDPOINTS_WITH_ASPECT_RATIO and request.aspect_ratio:
            body["aspect_ratio"] = request.aspect_ratio

        # 8) Fill images per endpoint; reference audio enters sounds in request order, numbering prompt “参考音频N” accordingly.
        if endpoint == "/reference2video":
            body["images"] = [image_to_data_uri(p) for p in _reference_images(request)]
            if audio_files:
                body["sounds"] = [
                    reference_audio_to_data_uri(p, model=self._model, mime_types=_REFERENCE_AUDIO_MIME_TYPES)
                    for p in audio_files
                ]
        elif endpoint == "/start-end2video":
            assert request.start_image is not None
            assert request.end_image is not None
            body["images"] = [
                image_to_data_uri(Path(request.start_image)),
                image_to_data_uri(Path(request.end_image)),
            ]
        elif endpoint == "/img2video":
            assert request.start_image is not None
            body["images"] = [image_to_data_uri(Path(request.start_image))]
        # /text2video omits images.

        return endpoint, body

    def _select_endpoint(self, request: VideoGenerationRequest) -> str:
        # Explicit start/end_image with missing files must fail, never silently degrade to text2video.
        if request.start_image is not None and not Path(request.start_image).exists():
            raise FileNotFoundError(f"start_image 文件不存在: {request.start_image}")
        if request.end_image is not None and not Path(request.end_image).exists():
            raise FileNotFoundError(f"end_image 文件不存在: {request.end_image}")
        return _endpoint_for(request)

    @classmethod
    def metered_usage(
        cls,
        model: str,
        request: VideoGenerationRequest,
        result: VideoGenerationResult | None = None,
        *,
        entry: CatalogEntry | None = None,
    ) -> Usage:
        """This request's measurements (:mod:`lib.backends.video_backends.metering`).

        Duration/reference counts use adjusted sent values from ``_build_request``; resolution retains request values. Endpoint determines reference-to-video,
        whose Vidu prices differ from image/text-to-video. Audio/duration adjustments read selected endpoints, sharing request-construction sources.
        """
        del model, entry
        endpoint = _endpoint_for(request)
        seconds = _coerce_duration(cls.endpoint, endpoint, request.duration_seconds)
        return video_usage(
            request,
            result,
            seconds=seconds,
            resolution=request.resolution or None,
            audio=bool(request.generate_audio) if cls.endpoint.audio else False,
            reference_images=len(_reference_images(request)) if endpoint == "/reference2video" else 0,
            reference_to_video=endpoint == "/reference2video",
        )

    # ── HTTP wrappers ───────────────────────────────────────────────────

    @with_retry_async(retry_if=should_retry_submit)
    async def _create_task(
        self, client, endpoint: str, body: dict, request: VideoGenerationRequest | None = None
    ) -> dict:
        assert_vidu_body_size(body)
        logger.info(
            "调用 Vidu 视频 API endpoint=%s kwargs=%s",
            endpoint,
            format_kwargs_for_log(safe_body_for_log(body)),
        )
        # create is nonidempotent creation + billing: submit_post converts ambiguous states (ReadTimeout etc.,
        # meaning requests may have reached servers) into terminal AmbiguousSubmitError without retries. Only connections
        # known not to have delivered requests retry through should_retry_submit, preventing duplicate tasks/billing.
        # 4xx such as 413 propagate through raise_for_status as httpx.HTTPStatusError with status_code,
        # allowing the choke point to recognize 413 for tier-fallback retries; submit_post logs bodies for diagnostics.
        resp = await submit_post(lambda: client.post(endpoint, json=body), provider=PROVIDER_VIDU, request=request)
        data = resp.json()
        if not data.get("task_id"):
            raise RuntimeError(f"Vidu 视频任务创建响应缺少 task_id: {data}")
        return data

    async def _download_video(self, url: str, output_path: Path) -> None:
        """Download completed CDN video under shared artifact retry budgets.

        Retrieval is ordinary URL GET, not SDK calls, raising ``HTTPStatusError``; use shared predicates:
        newly issued terminal URLs may not have propagated to CDN, so retry 403/404 instead of abandoning paid outputs.
        """
        await download_video(url, output_path, label="Vidu", retryable_errors=VIDU_RETRYABLE_ERRORS)


def _endpoint_for(request: VideoGenerationRequest) -> str:
    """Dispatch: reference images → reference-to-video; first/last frames → first-last-frame video; first frame → image-to-video; otherwise text-to-video."""
    if any(request.reference_images or []):
        return "/reference2video"
    if request.start_image is not None and request.end_image is not None:
        return "/start-end2video"
    if request.start_image is not None:
        return "/img2video"
    return "/text2video"


def _reference_images(request: VideoGenerationRequest) -> list[Path]:
    """Sent reference-to-video images (count limits validated by ``_build_request``)."""
    return [Path(p) for p in (request.reference_images or []) if p]


def _coerce_duration(profile: ViduEndpoint, endpoint: str, requested: int | None) -> int:
    """Adjust requested duration to the closest legal value for each selected endpoint subpath."""
    allowed = profile.durations.get(endpoint)
    if not allowed:
        # No forced validation without table entries (let API defaults apply), but pass supplied values through.
        return int(requested) if requested else 5
    if requested is None:
        return allowed[0] if 5 not in allowed else 5
    if requested in allowed:
        return int(requested)
    nearest = min(allowed, key=lambda v: abs(v - requested))
    logger.warning(
        "Vidu duration %s 不在 model=%s endpoint=%s 合法集合 %s，使用最近值 %s",
        requested,
        profile.key,
        endpoint,
        allowed,
        nearest,
    )
    return int(nearest)


class ViduGenericVideoBackend(ViduVideoBackend):
    """``vidu-video-generic`` request contract."""

    endpoint = VIDU_GENERIC


class ViduQ3ProVideoBackend(ViduVideoBackend):
    """``vidu-video-q3-pro`` request contract."""

    endpoint = VIDU_Q3_PRO


class ViduQ3TurboVideoBackend(ViduVideoBackend):
    """``vidu-video-q3-turbo`` request contract."""

    endpoint = VIDU_Q3_TURBO


class ViduQ3FastVideoBackend(ViduVideoBackend):
    """``vidu-video-q3-fast`` request contract."""

    endpoint = VIDU_Q3_FAST


class ViduQ3ReferenceVideoBackend(ViduVideoBackend):
    """``vidu-video-q3-reference`` request contract."""

    endpoint = VIDU_Q3_REFERENCE


class ViduQ2VideoBackend(ViduVideoBackend):
    """``vidu-video-q2`` request contract."""

    endpoint = VIDU_Q2


class ViduQ2ProVideoBackend(ViduVideoBackend):
    """``vidu-video-q2-pro`` request contract."""

    endpoint = VIDU_Q2_PRO


class ViduQ2FastVideoBackend(ViduVideoBackend):
    """``vidu-video-q2-fast`` request contract."""

    endpoint = VIDU_Q2_FAST


class ViduQ1VideoBackend(ViduVideoBackend):
    """``vidu-video-q1`` request contract."""

    endpoint = VIDU_Q1


class ViduQ1ClassicVideoBackend(ViduVideoBackend):
    """``vidu-video-q1-classic`` request contract."""

    endpoint = VIDU_Q1_CLASSIC


class Vidu2VideoBackend(ViduVideoBackend):
    """``vidu-video-2`` request contract."""

    endpoint = VIDU_2


class ViduQ4VideoBackend(ViduVideoBackend):
    """``vidu-video-q4`` request contract."""

    endpoint = VIDU_Q4


VIDU_BACKENDS: Mapping[str, type[ViduVideoBackend]] = {
    backend.endpoint.key: backend
    for backend in (
        ViduGenericVideoBackend,
        ViduQ3ProVideoBackend,
        ViduQ3TurboVideoBackend,
        ViduQ3FastVideoBackend,
        ViduQ3ReferenceVideoBackend,
        ViduQ2VideoBackend,
        ViduQ2ProVideoBackend,
        ViduQ2FastVideoBackend,
        ViduQ1VideoBackend,
        ViduQ1ClassicVideoBackend,
        Vidu2VideoBackend,
        ViduQ4VideoBackend,
    )
}
