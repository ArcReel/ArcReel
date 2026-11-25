"""ViduVideoBackend — Vidu 视频生成后端。

按 request 字段分派到 4 个 Vidu API 端点：
- 仅 prompt              → ``/text2video``
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
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.backend_runtime import (
    download_video,
    poll_with_retry,
    read_video_images,
    recording_poll,
    reference_audio_to_data_uri,
    should_retry_submit,
    submit_post,
)
from lib.backends.input_limits import excess_image_names
from lib.backends.providers import PROVIDER_VIDU
from lib.backends.transport_layer import TransportLayer
from lib.backends.video_backends.metering import video_usage
from lib.backends.video_backends.name_family import EntryBoundCapabilities
from lib.backends.video_capabilities import VideoEndpointCapabilities, audio_protocol
from lib.backends.vidu_shared import (
    VIDU_RETRYABLE_ERRORS,
    assert_vidu_body_size,
    create_vidu_client,
    extract_vidu_url,
    fetch_vidu_task,
    is_vidu_done,
    safe_body_for_log,
    vidu_failure_reason,
)
from lib.billing.metering import Usage
from lib.capabilities.kernel import DurationCorrection, Endpoint, ModelDurations
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "viduq3-turbo"
_REFERENCE_AUDIO_MIME_TYPES = {".mp3": "video/mp3"}


@dataclass(frozen=True)
class ViduEndpoint:
    """固定的音轨字段、各执行子路径的时长契约（ADR 0104）与输入上限。

    ``audio`` 表示请求体带音轨开关 ``audio``；不带开关时，``native_audio`` 表示成片固定有声（音画同出），否则无声。
    ``max_prompt_chars`` 是文生、图生、首尾帧的提示词上限，``max_reference_prompt_chars`` 是参考生视频的；
    ``max_reference_images`` 与 ``max_reference_audio`` 是参考生视频一次能带的参考图张数与参考音频段数，
    参考音频为 0 时不收。``default_duration`` 是未指定时长时发送的秒数，为空时取 5 秒或时长表首项。
    ``reference_aspect_ratios`` 是参考生视频接受的画面比例，为空时不在本地校验。
    """

    key: str
    audio: bool
    durations: Mapping[str, tuple[int, ...]]
    max_reference_images: int = 7
    max_prompt_chars: int = 5000
    max_reference_prompt_chars: int = 2000
    max_reference_audio: int = 0
    native_audio: bool = False
    default_duration: int | None = None
    reference_aspect_ratios: frozenset[str] = frozenset()

    @property
    def duration_correction(self) -> DurationCorrection:
        return DurationCorrection(self.durations, fallback=5, preferred=self.default_duration)

    @property
    def audio_track(self) -> VideoAudioMode:
        """成片音轨：带开关时可控；不带开关时按模型是否音画同出固定有声或无声。"""
        if self.audio:
            return VideoAudioMode.CONTROLLABLE
        return VideoAudioMode.ALWAYS_ON if self.native_audio else VideoAudioMode.ALWAYS_OFF

    def output_audio(self, requested: bool) -> bool:
        """成片是否有声：带开关时取请求意图，否则取端点固定值。"""
        return bool(requested) if self.audio else self.native_audio

    def prompt_limit(self, path: str) -> int:
        """发往子路径 ``path`` 的提示词上限。"""
        return self.max_reference_prompt_chars if path == "/reference2video" else self.max_prompt_chars

    @property
    def declared_prompt_limit(self) -> int:
        """无请求上下文时声明的提示词上限：端点只服务参考生视频时取参考生视频的上限，否则取其余子路径的上限。

        多子路径端点走参考生视频时更窄的上限由 ``_build_request`` 按实际子路径兜底。
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
    max_reference_prompt_chars=5000,
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
VIDU_Q4 = ViduEndpoint(
    "vidu-video-q4",
    True,
    {"/img2video": tuple(range(3, 17)), "/reference2video": tuple(range(3, 17))},
    max_reference_images=15,
    max_prompt_chars=20000,
    max_reference_prompt_chars=20000,
    max_reference_audio=3,
)
_Q3_WIDE_ASPECT_RATIOS = frozenset({"1:1", "16:9", "9:16", "3:4", "4:3", "auto"})
VIDU_Q3_DRAMA = ViduEndpoint(
    "vidu-video-q3-drama",
    True,
    {"/reference2video": tuple(range(2, 17))},
    max_reference_prompt_chars=20000,
    max_reference_audio=3,
    default_duration=8,
    reference_aspect_ratios=frozenset({"16:9", "9:16", "auto"}),
)
VIDU_Q3_MIX = ViduEndpoint(
    "vidu-video-q3-mix",
    False,
    {"/reference2video": tuple(range(3, 16))},
    max_reference_prompt_chars=20000,
    native_audio=True,
    reference_aspect_ratios=_Q3_WIDE_ASPECT_RATIOS,
)
VIDU_Q3_AD = ViduEndpoint(
    "vidu-video-q3-ad",
    False,
    {"/reference2video": tuple(range(3, 16))},
    max_reference_prompt_chars=20000,
    reference_aspect_ratios=_Q3_WIDE_ASPECT_RATIOS,
)


_ENDPOINTS_WITH_ASPECT_RATIO = frozenset({"/text2video", "/reference2video"})


class ViduVideoBackend(VideoEndpointCapabilities, EntryBoundCapabilities):
    """Vidu 视频生成后端，按 request 字段分派到不同端点。"""

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
    def describe_video_endpoint(cls, model: str, entry: CatalogEntry | None = None) -> Endpoint:
        profile = cls.endpoint
        slots = frozenset({"text_to_video", "first_frame", "last_frame", "reference_images"})
        if profile.max_reference_audio:
            slots |= {"reference_audio"}
        if not profile.max_reference_images:
            slots -= {"reference_images"}
        return Endpoint(
            "video",
            slots,
            audio=audio_protocol(profile.audio_track),
            duration=ModelDurations(profile.duration_correction),
            exclusive_inputs=(
                frozenset({"first_frame", "reference_images"}),
                frozenset({"last_frame", "reference_images"}),
            ),
            limits={
                "max_prompt_chars": profile.declared_prompt_limit,
                "max_reference_images": profile.max_reference_images,
                "max_reference_audio_count": profile.max_reference_audio,
            },
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model, self._catalog_entry)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        raise NotImplementedError("ViduVideoBackend 暂不支持 resume_video")

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        endpoint, body = await asyncio.to_thread(self._build_request, request)
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
            generate_audio=(request.video_capabilities or self.video_capabilities)
            .audio_track_for_route("r2v" if request.reference_images else "i2v")
            .output_audio(request.generate_audio),
        )

    def _build_request(self, request: VideoGenerationRequest) -> tuple[str, dict]:
        endpoint = self._select_endpoint(request)

        profile = self.endpoint
        audio_files = [Path(p) for p in request.reference_audio_files or []]
        if audio_files:
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
                    names=excess_image_names(profile.max_reference_images, count, request.reference_images or []),
                    model=self._model,
                    limit=profile.max_reference_images,
                    count=count,
                )
            allowed_ratios = profile.reference_aspect_ratios
            if allowed_ratios and request.aspect_ratio and request.aspect_ratio not in allowed_ratios:
                raise VideoCapabilityError(
                    "video_aspect_ratio_not_supported",
                    model=self._model,
                    aspect_ratio=request.aspect_ratio,
                    supported=", ".join(sorted(allowed_ratios)),
                )
        if endpoint == "/text2video" and request.end_image is not None:
            raise VideoCapabilityError("video_end_image_requires_start_image", model=self._model)

        duration = _coerce_duration(profile, endpoint, request.duration_seconds)

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

        if request.resolution:
            body["resolution"] = request.resolution

        if request.seed is not None:
            body["seed"] = request.seed

        if self.endpoint.audio:
            body["audio"] = bool(request.generate_audio)
        elif (
            self.endpoint.native_audio
            and not request.generate_audio
            and (request.video_capabilities or self.video_capabilities)
            .audio_track_for_route("r2v" if request.reference_images else "i2v")
            .has_audio_track
        ):
            raise VideoCapabilityError("video_audio_switch_not_supported", provider=self.name, model=self._model)

        if endpoint in _ENDPOINTS_WITH_ASPECT_RATIO and request.aspect_ratio:
            body["aspect_ratio"] = request.aspect_ratio

        images = read_video_images(request, self._model)
        if endpoint == "/reference2video":
            body["images"] = [images[Path(p)].data_uri() for p in _reference_images(request)]
            if audio_files:
                body["sounds"] = [
                    reference_audio_to_data_uri(p, model=self._model, mime_types=_REFERENCE_AUDIO_MIME_TYPES)
                    for p in audio_files
                ]
        elif endpoint == "/start-end2video":
            assert request.start_image is not None
            assert request.end_image is not None
            body["images"] = [
                images[Path(request.start_image)].data_uri(),
                images[Path(request.end_image)].data_uri(),
            ]
        elif endpoint == "/img2video":
            assert request.start_image is not None
            body["images"] = [images[Path(request.start_image)].data_uri()]

        return endpoint, body

    def _select_endpoint(self, request: VideoGenerationRequest) -> str:
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
        """这次请求的计量（见 :mod:`lib.backends.video_backends.metering`）。

        时长与参考图张数取 ``_build_request`` 校正后的发送值，分辨率取请求原值；参考生视频按端点判定，Vidu 的
        参考生视频与图生、文生视频单价不同。音轨开关与时长校正均读所选端点，与请求构造同源。
        """
        del model, entry
        endpoint = _endpoint_for(request)
        seconds = _coerce_duration(cls.endpoint, endpoint, request.duration_seconds)
        return video_usage(
            request,
            result,
            seconds=seconds,
            resolution=request.resolution or None,
            audio=cls.endpoint.output_audio(request.generate_audio),
            reference_images=len(_reference_images(request)) if endpoint == "/reference2video" else 0,
            reference_to_video=endpoint == "/reference2video",
        )

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
        resp = await submit_post(lambda: client.post(endpoint, json=body), provider=PROVIDER_VIDU, request=request)
        data = resp.json()
        if not data.get("task_id"):
            raise RuntimeError(f"Vidu 视频任务创建响应缺少 task_id: {data}")
        return data

    async def _download_video(self, url: str, output_path: Path) -> None:
        """下载 CDN 上的成片，重试走共用的产物下载预算。

        取件是普通 URL GET（不是 SDK 调用），抛的就是 ``HTTPStatusError``，故用共用谓词：
        终态刚签发的地址可能尚未在 CDN 侧传播，403/404 要重试而不是判死一条已付费的成片。
        """
        await download_video(url, output_path, label="Vidu", retryable_errors=VIDU_RETRYABLE_ERRORS)


def _endpoint_for(request: VideoGenerationRequest) -> str:
    """请求派发到哪个端点：有参考图 → 参考生视频；首尾帧 → 首尾帧生视频；首帧 → 图生视频；否则文生视频。"""
    if any(request.reference_images or []):
        return "/reference2video"
    if request.start_image is not None and request.end_image is not None:
        return "/start-end2video"
    if request.start_image is not None:
        return "/img2video"
    return "/text2video"


def _reference_images(request: VideoGenerationRequest) -> list[Path]:
    """参考生视频发出的参考图（张数上限由 ``_build_request`` 校验）。"""
    return [Path(p or "") for p in (request.reference_images or [])]


def _coerce_duration(profile: ViduEndpoint, endpoint: str, requested: int | None) -> int:
    """按调用端点在各子路径的合法集合，把请求时长校正到最近值。"""
    duration = profile.duration_correction.correct(endpoint, requested)
    if requested is not None and duration != requested:
        logger.warning(
            "Vidu duration %s 不在 model=%s endpoint=%s 合法集合 %s，使用最近值 %s",
            requested,
            profile.key,
            endpoint,
            profile.durations.get(endpoint),
            duration,
        )
    return duration


class ViduGenericVideoBackend(ViduVideoBackend):
    """``vidu-video-generic`` 请求契约。"""

    endpoint = VIDU_GENERIC


class ViduQ3ProVideoBackend(ViduVideoBackend):
    """``vidu-video-q3-pro`` 请求契约。"""

    endpoint = VIDU_Q3_PRO


class ViduQ3TurboVideoBackend(ViduVideoBackend):
    """``vidu-video-q3-turbo`` 请求契约。"""

    endpoint = VIDU_Q3_TURBO


class ViduQ3FastVideoBackend(ViduVideoBackend):
    """``vidu-video-q3-fast`` 请求契约。"""

    endpoint = VIDU_Q3_FAST


class ViduQ3ReferenceVideoBackend(ViduVideoBackend):
    """``vidu-video-q3-reference`` 请求契约。"""

    endpoint = VIDU_Q3_REFERENCE


class ViduQ3DramaVideoBackend(ViduVideoBackend):
    """``vidu-video-q3-drama`` 请求契约。"""

    endpoint = VIDU_Q3_DRAMA


class ViduQ3MixVideoBackend(ViduVideoBackend):
    """``vidu-video-q3-mix`` 请求契约。"""

    endpoint = VIDU_Q3_MIX


class ViduQ3AdVideoBackend(ViduVideoBackend):
    """``vidu-video-q3-ad`` 请求契约。"""

    endpoint = VIDU_Q3_AD


class ViduQ2VideoBackend(ViduVideoBackend):
    """``vidu-video-q2`` 请求契约。"""

    endpoint = VIDU_Q2


class ViduQ2ProVideoBackend(ViduVideoBackend):
    """``vidu-video-q2-pro`` 请求契约。"""

    endpoint = VIDU_Q2_PRO


class ViduQ2FastVideoBackend(ViduVideoBackend):
    """``vidu-video-q2-fast`` 请求契约。"""

    endpoint = VIDU_Q2_FAST


class ViduQ1VideoBackend(ViduVideoBackend):
    """``vidu-video-q1`` 请求契约。"""

    endpoint = VIDU_Q1


class ViduQ1ClassicVideoBackend(ViduVideoBackend):
    """``vidu-video-q1-classic`` 请求契约。"""

    endpoint = VIDU_Q1_CLASSIC


class Vidu2VideoBackend(ViduVideoBackend):
    """``vidu-video-2`` 请求契约。"""

    endpoint = VIDU_2


class ViduQ4VideoBackend(ViduVideoBackend):
    """``vidu-video-q4`` 请求契约。"""

    endpoint = VIDU_Q4


VIDU_BACKENDS: Mapping[str, type[ViduVideoBackend]] = {
    backend.endpoint.key: backend
    for backend in (
        ViduGenericVideoBackend,
        ViduQ3ProVideoBackend,
        ViduQ3TurboVideoBackend,
        ViduQ3FastVideoBackend,
        ViduQ3ReferenceVideoBackend,
        ViduQ3DramaVideoBackend,
        ViduQ3MixVideoBackend,
        ViduQ3AdVideoBackend,
        ViduQ2VideoBackend,
        ViduQ2ProVideoBackend,
        ViduQ2FastVideoBackend,
        ViduQ1VideoBackend,
        ViduQ1ClassicVideoBackend,
        Vidu2VideoBackend,
        ViduQ4VideoBackend,
    )
}
