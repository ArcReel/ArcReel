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
# 参考音频的 data URI：官方只收 mp3，内容类型字符串按官方参考生视频示例写 data:video/mp3。
_REFERENCE_AUDIO_MIME_TYPES = {".mp3": "video/mp3"}


@dataclass(frozen=True)
class ViduEndpoint:
    """固定的音轨字段、各执行子路径的时长契约（ADR 0018）与输入上限。

    ``max_prompt_chars`` 是文生、图生、首尾帧的提示词上限，``max_reference_prompt_chars`` 是参考生视频的；
    ``max_reference_images`` 与 ``max_reference_audio`` 是参考生视频一次能带的参考图张数与参考音频段数，
    参考音频为 0 时不收。
    """

    key: str
    audio: bool
    durations: Mapping[str, tuple[int, ...]]
    max_reference_images: int = 7
    max_prompt_chars: int = 5000
    max_reference_prompt_chars: int = 2000
    max_reference_audio: int = 0

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
# viduq4-preview：没有文生与首尾帧；参考生视频收 1～15 张参考图与 0～3 段参考音频，提示词上限 20000。
VIDU_Q4 = ViduEndpoint(
    "vidu-video-q4",
    True,
    {"/img2video": tuple(range(3, 17)), "/reference2video": tuple(range(3, 17))},
    max_reference_images=15,
    max_prompt_chars=20000,
    max_reference_prompt_chars=20000,
    max_reference_audio=3,
)


# 端点是否接受 aspect_ratio
_ENDPOINTS_WITH_ASPECT_RATIO = frozenset({"/text2video", "/reference2video"})


class ViduVideoBackend(EntryBoundCapabilities):
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
    def video_capabilities_for_model(cls, model: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
        """Vidu 调用通道的声明（通道事实）—— 纯计算，不构造 client。

        通道按请求字段分派到 /text2video、/img2video、/start-end2video、/reference2video 四个端点，对任何型号都
        发得出文生、首帧、首尾帧与参考图。各型号接受哪些输入记在模型条目上，与本声明两侧求值
        （``lib.backends.video_capabilities``，docs/adr/0104）。参考图与首尾帧在 Vidu 上是互斥模式：
        _select_endpoint 见参考图即切 /reference2video，该端点不收首尾帧，_build_request 拒绝两者同带的请求。

        音轨开关、子路径时长、参考图张数、参考音频段数与提示词上限都由所选端点声明（``_build_request`` 同读），
        与型号名无关。``model`` 与 ``entry`` 是能力契约的统一入参，本通道不读。
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
        # Vidu 不支持 resume：poll 完全内联在 generate，没有可供接续的独立轮询入口。
        # orphan handler 据 NotImplementedError 标 [resume_unsupported]
        raise NotImplementedError("ViduVideoBackend 暂不支持 resume_video")

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        endpoint, body = await asyncio.to_thread(self._build_request, request)
        # _build_request 已把 duration 归一化到 body["duration"]，统一使用以避免 None 崩溃及结果不一致。
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
            # credits=0 是合法值，不能用 `or` 合并；显式判 None 兜底为顶层 credits。
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

        # 0) 端点发不出的输入付费前拒绝，不静默丢弃：能力被用户覆盖放宽时 gate 会放行它们。
        profile = self.endpoint
        audio_files = [Path(p) for p in request.reference_audio_files or []]
        if audio_files:
            # 只有参考生视频接口收参考音频（sounds）
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

        # 1) 型号接不接受这条端点的输入由模型条目与通道两侧求值后在 gate_video_request 处判定（docs/adr/0104），
        #    这里不按型号拒绝；条目未收录的输入由上游判定。

        # 2) duration 在合法集合内取最近值
        duration = _coerce_duration(profile, endpoint, request.duration_seconds)

        # 3) prompt 长度（上限按端点与子路径声明）
        # 超限一律拒绝，不再客户端截断：截断会产出与意图不符的成片却照常计费、用户无从知情，
        # 与 gate_video_request 「同一种违约只有硬失败、没有静默降级」的口径一致。model 级上限
        # （video_capabilities_for_model 声明的那个）已由 gate 在付费前拦下；此处是端点级更窄
        # 上限的执行期防御——多端点 model 走 /reference2video 时 gate 的静态声明覆盖不到。
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

        # 4) resolution 以模型目录条目为准，原样透传；越界档位由上游拒绝（docs/adr/0104）
        if request.resolution:
            body["resolution"] = request.resolution

        # 5) seed
        if request.seed is not None:
            body["seed"] = request.seed

        # 6) audio 仅 q3 系列接受
        if self.endpoint.audio:
            body["audio"] = bool(request.generate_audio)

        # 7) aspect_ratio 仅 text2video / reference2video 接受
        if endpoint in _ENDPOINTS_WITH_ASPECT_RATIO and request.aspect_ratio:
            body["aspect_ratio"] = request.aspect_ratio

        # 8) images 按端点形态填充；参考音频按请求顺序放进 sounds，prompt 里的「参考音频N」按这个顺序编号
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
        # /text2video 不带 images

        return endpoint, body

    def _select_endpoint(self, request: VideoGenerationRequest) -> str:
        # 若用户显式传了 start/end_image 但文件不存在，应直接失败而不是静默降级到 text2video。
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
        # create 是非幂等的「创建 + 计费」调用：submit_post 把歧义态（ReadTimeout 等
        # 「请求可能已送达服务端」）转 AmbiguousSubmitError 终态失败、不重试，仅「请求确定
        # 未送达」的连接建立失败交 should_retry_submit 重试，避免重复建任务 + 重复计费。
        # 413 等 4xx 经 raise_for_status 透出 httpx.HTTPStatusError（保留 status_code），
        # 让咽喉层识别 413 走降档重试；body 由 submit_post 落日志保留可诊断性。
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
    return [Path(p) for p in (request.reference_images or []) if p]


def _coerce_duration(profile: ViduEndpoint, endpoint: str, requested: int | None) -> int:
    """按调用端点在各子路径的合法集合，把请求时长校正到最近值。"""
    allowed = profile.durations.get(endpoint)
    if not allowed:
        # 表里无项时不强校（让接口按默认处理），但若给了值就透传
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
        ViduQ2VideoBackend,
        ViduQ2ProVideoBackend,
        ViduQ2FastVideoBackend,
        ViduQ1VideoBackend,
        ViduQ1ClassicVideoBackend,
        Vidu2VideoBackend,
        ViduQ4VideoBackend,
    )
}
