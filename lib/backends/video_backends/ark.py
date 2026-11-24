"""ArkVideoBackend — 火山方舟 Ark 视频生成后端。"""

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
    ResumeExpiredError,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.ark_shared import ark_base_url, create_ark_client
from lib.backends.backend_runtime import (
    ProviderJobIdPersistenceMixin,
    download_video,
    poll_with_retry,
    read_video_images,
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
from lib.backends.video_capabilities import VideoEndpointCapabilities
from lib.billing.metering import Usage
from lib.capabilities.kernel import AudioProtocol, Endpoint
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

_REFERENCE_AUDIO_MIME_TYPES = {".wav": "audio/wav", ".mp3": "audio/mp3"}

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

_CATALOG_SERVICE_TIERS = {"default": "standard"}


@dataclass(frozen=True)
class SeedanceEndpoint:
    """一个 Seedance 内置调用端点的请求结构与计量（通道事实，与型号名无关）。

    Seedance 1、2、2.5 共用同一个创建任务与轮询接口，差别在请求体、计量和素材引用的契约上，各是一个调用端点。
    同一端点下各型号接受哪些输入是模型事实，记在模型条目上（``lib.backends.video_capabilities``）。
    """

    key: str
    sends_service_tier: bool
    token_pixels: Mapping[tuple[str, str], tuple[int, int]] | None
    reference_image_asset: bool
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
    """产物下载共用谓词 ⊕ Ark 特有的「任务已成功但产物未就绪」形态（400 video_not_ready）。"""
    if (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code == 400
        and "video_not_ready" in str(exc.response.text)
    ):
        return True
    return should_retry_download(exc)


def _safe_create_params_for_log(create_params: dict[str, Any]) -> dict[str, Any]:
    """请求参数的安全日志视图：content 数组按条目类型折叠成计数，不展开内嵌素材。

    content 里的图片与音频都是 base64 data URI。``format_kwargs_for_log`` 只截断长字符串、
    不认识这层结构，直接交给它会把每个素材的 base64 前缀写进 info 日志（音频还可能带上
    mp3 文件头的 ID3 元数据）。dashscope / vidu / minimax 三家各有同名的安全视图，同为
    对齐 CodeQL clear-text-logging。

    prompt 是用户文本、不是素材，仍按 ``format_kwargs_for_log`` 的既有截断规则输出，
    以保留排查生成效果时最常用的那一项。
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
    """与参考图等长同序的素材引用；请求没有给出时全部为 None。"""
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
    """方舟以「输入图片未通过审核」拒绝时的结构化错误；其他错误返回 None。

    方舟的错误不给出被拒图片的位置，所以只有请求里恰好一张图片时才能确定是哪一张：这时点名它，
    其余情况不猜测。
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
    """轮询到的终态失败：输入图片被拒时抛结构化错误，其余失败返回原因文本，未失败返回 None。"""
    if task.status not in ("failed", "expired"):
        return None
    error = getattr(task, "error", None)
    rejection = _input_image_rejection(request, getattr(error, "code", None), getattr(error, "message", None))
    if rejection is not None:
        raise rejection
    return f"Ark 视频生成失败(status={task.status}): {error or 'Unknown error'}"


class ArkVideoBackend(VideoEndpointCapabilities, ProviderJobIdPersistenceMixin):
    """Ark (火山方舟) 视频生成后端：Seedance 各代端点的公共实现，请求结构与计量的差异由 :attr:`endpoint` 给出。

    三个端点各是一个子类（:class:`Seedance1VideoBackend` 等），注册表、能力声明与计量按子类认定端点，不按型号名。
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
        self._api_key = api_key
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
        """本实例发请求实际使用的推理凭证，素材库按它的绑定选用供应商素材；装配时没有给出则为 None。"""
        return self._inference_credential

    @property
    def provider_asset_roles(self) -> frozenset[str]:
        """可以用 ``asset://`` 素材引用代替图片内容的槽位，由端点声明（见 :attr:`SeedanceEndpoint.reference_image_asset`）。

        首帧、尾帧没有官方契约，不声明；未声明的槽位照常发送图片内容。
        """
        return frozenset({REFERENCE_IMAGE_ROLE}) if self.endpoint.reference_image_asset else frozenset()

    @property
    def model(self) -> str:
        return self._model

    @classmethod
    def estimate_video_tokens(cls, *, resolution: str | None, ratio: str, seconds: int) -> float | None:
        """按官方公式估算一次请求的视频 token；端点不按视频 token 计量、宽高比为 ``adaptive`` 或档位不在像素表里时为 ``None``。

        内置调用通道不发送输入视频，输入视频时长恒为 0。
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
        """这次请求的计量（见 :mod:`lib.backends.video_backends.metering`），按端点认定，与型号名和条目无关。

        视频 token 结算取供应商回报的 ``completion_tokens``（``result.usage_tokens``），预估按
        :meth:`estimate_video_tokens` 换算。服务档只在请求体带它的端点上上报，与请求构造同一判定。
        ``model`` 与 ``entry`` 是计量契约的统一入参，本通道不读。
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
    def describe_video_endpoint(cls, model: str, entry: CatalogEntry | None = None) -> Endpoint:
        return Endpoint(
            "video",
            frozenset({"text_to_video", "first_frame", "last_frame", "reference_images", "reference_audio"}),
            audio=AudioProtocol.SWITCH,
            defaults={"first_frame_ratio_adaptive_only": cls.endpoint.first_frame_ratio_adaptive_only},
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """生成视频。任务创建和轮询阶段分离重试，避免瞬态错误导致重建任务。"""
        try:
            provider_task_id = await self._create_task(request)
        except Exception as exc:
            body = getattr(exc, "body", None)
            reason = body.get("message") if isinstance(body, dict) else getattr(exc, "message", None)
            rejection = _input_image_rejection(request, getattr(exc, "code", None), reason)
            if rejection is not None:
                raise rejection from exc
            raise
        await self._persist_provider_job_id(request, provider_task_id, provider=PROVIDER_ARK, endpoint=self._base_url)
        return await self._poll_until_done(provider_task_id, request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 Ark task：仅 poll + 下载。

        Ark 端 task 不存在/已过期通常表现为 SDK 抛 404 类异常或返回 status='expired'；
        本接口在 _poll_until_done 内识别后者，前者由本方法拦截转 ResumeExpiredError。
        """
        try:
            return await self._poll_until_done(job_id, request)
        except Exception as exc:
            if _is_ark_not_found(exc):
                raise ResumeExpiredError(job_id=job_id, provider=PROVIDER_ARK) from exc
            raise

    @with_retry_async()
    async def _create_task(self, request: VideoGenerationRequest) -> str:
        """创建 Ark 视频生成任务（带重试保护）。"""
        content: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]

        content.extend(await asyncio.to_thread(self._build_image_content, request))

        if request.reference_audio_files:
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
        if self.endpoint.sends_service_tier:
            create_params["service_tier"] = request.service_tier
        if request.seed is not None:
            create_params["seed"] = request.seed

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
        """单独重试视频下载，避免下载失败导致重新生成视频而浪费额度。

        Ark 的视频 URL 在任务 succeeded 后可能仍未就绪，此时返回的是 400 video_not_ready
        而非产物通道常见的 403/404，故在共用谓词之上补这一条。
        """
        await download_video(video_url, output_path, label="Ark", retry_if=_retry_ark_download)

    def _build_image_content(self, request: VideoGenerationRequest) -> list[dict[str, Any]]:
        """首帧 / 尾帧 / 参考图 → image_url 条目（同步：读本地图片，调用方放线程里跑）。"""
        asset_uris = _reference_asset_uris(request)
        if any(asset_uris) and REFERENCE_IMAGE_ROLE not in self.provider_asset_roles:
            raise ValueError(f"model {self._model} does not accept provider asset references")
        remote = frozenset(i for i, uri in enumerate(asset_uris, 1) if uri is not None)
        images = read_video_images(request, self._model, remote_indices=remote)
        content = []
        for role, path in (("first_frame", request.start_image), ("last_frame", request.end_image)):
            if path is not None:
                content.append({"type": "image_url", "image_url": {"url": images[Path(path)].data_uri()}, "role": role})
        for path, uri in zip(request.reference_images or [], asset_uris, strict=True):
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": uri or images[Path(path)].data_uri()},
                    "role": "reference_image",
                }
            )
        return content

    @contextmanager
    def _polling_client(self, request: VideoGenerationRequest) -> Generator[Any]:
        """轮询用的 SDK client：续跑且提交域名与当下配置不同时，按同一份凭据另建一个绑定提交域名的。

        任务 id 只在创建它的 endpoint 上可查，用户在途改 base_url 后按新域名去轮旧任务会 404，
        被 ``resume_video`` 误判成过期。提交路径的 ``submitted_base_url`` 恒 None，沿用实例 client。
        另建的 client 持有独立连接池，轮询结束即关闭。
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
        """轮询任务状态直到完成，瞬态错误仅重试当次轮询请求。"""
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

        video_url = result.content.video_url
        await self._download_video_with_retry(video_url, request.output_path)

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
    """``ark-seedance-1``：Seedance 1.x 的请求结构与计量。"""

    endpoint = SEEDANCE_1


class Seedance2VideoBackend(ArkVideoBackend):
    """``ark-seedance-2``：Seedance 2.0 的请求结构与计量。"""

    endpoint = SEEDANCE_2


class Seedance25VideoBackend(ArkVideoBackend):
    """``ark-seedance-2-5``：Seedance 2.5 的请求结构与计量。"""

    endpoint = SEEDANCE_2_5


SEEDANCE_BACKENDS: Mapping[str, type[ArkVideoBackend]] = {
    backend.endpoint.key: backend for backend in (Seedance1VideoBackend, Seedance2VideoBackend, Seedance25VideoBackend)
}


def _is_ark_not_found(exc: BaseException) -> bool:
    """识别 Ark 任务「不存在 / 已过期」响应。

    精确匹配官方稳定 ``task_not_found`` 错误码；移除宽泛的 ``"not found"`` 子串兜底，
    避免业务侧错误（如 reference image not found）被误判为 provider 端任务过期。
    ``expired`` 字串保留：Ark 自身 ``_poll_until_done`` 把 status in (failed, expired)
    转成 ``RuntimeError("Ark 任务失败 ... status=expired")``，要靠该字串识别回 ResumeExpiredError。
    """
    status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if status_code == 404:
        return True
    msg = str(exc).lower()
    return "task_not_found" in msg or "tasknotfound" in msg or "expired" in msg
