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

# 参考音频的 data URI MIME：官方接受 wav / mp3 两种格式，要求 `data:audio/<格式>;base64,<内容>`
# 且格式小写。资产上传期已按同一组格式收窄，此处按扩展名回填，未知扩展名不猜测直接拒绝。
# 与 dashscope 侧的同名表刻意各存一份：mp3 在这里按官方示例写 audio/mp3，dashscope 走标准
# 的 audio/mpeg——供应商各自的接受口径，合并成一张共享表会让其中一家收到没验证过的 MIME。
_REFERENCE_AUDIO_MIME_TYPES = {".wav": "audio/wav", ".mp3": "audio/mp3"}

# Seedance 视频 token 的官方估算公式（《模型价格》视频生成一节）：
#   (输入视频时长 + 输出视频时长) × 输出视频的宽 × 高 × 帧率 / 1024
# 帧率与各「分辨率 × 宽高比」的输出像素取自《创建视频生成任务 API》：输出帧率 24；2.5 与 2.0 系列只有 480p 的
# 16:9 / 9:16 两格不同，2.5 没有 4k。分辨率不发送时 API 默认 720p。结算以供应商回报的 completion_tokens 为准。
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

# 请求的服务档取值 → 价目表的服务档取值：方舟请求里的 ``default`` 即标准档。
_CATALOG_SERVICE_TIERS = {"default": "standard"}


@dataclass(frozen=True)
class SeedanceEndpoint:
    """一个 Seedance 内置调用端点的请求结构与计量（通道事实，与型号名无关）。

    Seedance 1、2、2.5 共用同一个创建任务与轮询接口，差别在请求体、计量和素材引用的契约上，各是一个调用端点。
    同一端点下各型号接受哪些输入是模型事实，记在模型条目上（``lib.backends.video_capabilities``）。
    """

    #: 内置调用端点名，与自定义供应商共用（``lib.custom_provider.endpoints.ENDPOINT_REGISTRY`` 的键）。
    key: str
    #: 请求体是否带 ``service_tier``：只有 1.x 接受；2.0 在参考生视频下以 400 拒绝该参数，2.5 按同代口径不带。
    sends_service_tier: bool
    #: 视频 token 预估用的「分辨率 × 宽高比 → 输出像素」表；``None`` 表示不按视频 token 预估（1.x 按次计价）。
    token_pixels: Mapping[tuple[str, str], tuple[int, int]] | None
    #: 参考图槽位是否接受 ``asset://`` 素材引用：官方《Seedance 人像素材使用指南》只给出 2.0 / 2.5 的契约。
    reference_image_asset: bool
    #: 首帧在场时比例是否只能交给上游按首帧自适应（2.5），见 ``VideoCapabilities.first_frame_ratio_adaptive_only``。
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


class ArkVideoBackend(ProviderJobIdPersistenceMixin):
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
        # 续跑回放提交域名时要按同一份凭据另建 client（SDK 在构造时绑定 base_url，没有换域名的公开方法）。
        self._api_key = api_key
        # 本实例请求实际发往的域名，与 create_ark_client 同一套归一化；提交时随 job_id 一并落库。
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
    def video_capabilities_for_model(cls, model: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
        """方舟视频调用通道的声明（通道事实）—— 纯计算，不构造 SDK client（无需 api_key），与型号名无关。

        通道对任何型号都按同一份请求构造发出首帧、尾帧（``role: first_frame / last_frame``）、参考图
        （``role: reference_image``）与参考音频（``role: reference_audio``），不限张数与段数。各型号接受哪些
        输入、能接受多少是模型事实，记在模型条目上，与本声明两侧求值（``lib.backends.video_capabilities``，
        docs/adr/0104）。首帧比例约束随端点声明（Seedance 2.5 在首帧在场时比例必须交给上游按首帧自适应，下发
        固定 ratio 会与首帧尺寸冲突）。``model`` 与 ``entry`` 是能力契约的统一入参，本通道不读。

        音轨形态全系取 ``VideoCapabilities`` 的默认值（开关可控）：``_build_payload`` 无条件把
        ``generate_audio`` 放进请求体，各档 Seedance 都认这个开关，故无需分支声明。
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
        # 一并写回本次实际请求的域名：续跑据此回放，否则在途改了 base_url 会按新域名去查旧任务。
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
        # 1. Build content list
        content: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]

        # Ark 视频 API 要求每个 image_url 条目在顶层带 `role` 字段
        # （first_frame / last_frame / reference_image），否则 400 InvalidParameter。
        # 存在性检查与读图编码都是阻塞 I/O，整体卸载到线程。
        content.extend(await asyncio.to_thread(self._build_image_content, request))

        if request.reference_audio_files:
            # 音频条目与图片条目同列 content 数组，各自按类型独立编号：prompt 里的「音频N」
            # 指的是第 N 个 type="audio_url" 条目（官方《快速入门》提示词规则）。故这里必须
            # 保持 reference_audio_files 的原始顺序、不跳过任何一段——跳过会让编排层拼好的
            # 指认文本整体错位，把 A 角色的音色安到 B 角色头上。gather 按入参顺序返回，
            # 并发不影响该编号。读整段音频做 base64 编码是阻塞 I/O，逐段卸载到线程。
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
        # 比例优先：ratio 是独立 SDK 字段，由 aspect_ratio 直接决定；resolution 仅清晰度档位，
        # 与比例正交（SDK 内部按 ratio×resolution 算像素），不把比例压进像素 size，故无尺寸 bug。
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
        # 只有 Seedance 1.x 端点接受 service_tier，见 SeedanceEndpoint.sends_service_tier
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
        """单独重试视频下载，避免下载失败导致重新生成视频而浪费额度。

        Ark 的视频 URL 在任务 succeeded 后可能仍未就绪，此时返回的是 400 video_not_ready
        而非产物通道常见的 403/404，故在共用谓词之上补这一条。
        """
        await download_video(video_url, output_path, label="Ark", retry_if=_retry_ark_download)

    def _build_image_content(self, request: VideoGenerationRequest) -> list[dict[str, Any]]:
        """首帧 / 尾帧 / 参考图 → image_url 条目（同步：读本地图片，调用方放线程里跑）。"""
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
                # 尾帧缺失不静默跳过：跳过后任务照常提交并计费，用户拿到一段没有尾帧、
                # 与分镜衔接不上的视频却无从知道原因。
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
            # 以素材引用发出的参考图不读本地内容，只核对其余参考图。
            missing = [
                p
                for r, uri in zip(request.reference_images, asset_uris, strict=True)
                if uri is None and not (p := Path(r)).is_file()
            ]
            if missing:
                # 与尾帧同理：少一张参考图仍会出片并计费，成片里的角色/场景却已跑偏。
                raise VideoCapabilityError(
                    "video_reference_images_unreadable",
                    model=self._model,
                    names=", ".join(p.name or str(p) for p in missing),
                )
            # 素材引用就地替换同一位置的图片内容，不增删、不重排：提示词里的「图片N」仍指向第 N 个参考图条目。
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
    """``ark-seedance-1``：Seedance 1.x 的请求结构与计量。"""

    endpoint = SEEDANCE_1


class Seedance2VideoBackend(ArkVideoBackend):
    """``ark-seedance-2``：Seedance 2.0 的请求结构与计量。"""

    endpoint = SEEDANCE_2


class Seedance25VideoBackend(ArkVideoBackend):
    """``ark-seedance-2-5``：Seedance 2.5 的请求结构与计量。"""

    endpoint = SEEDANCE_2_5


#: Seedance 内置调用端点名 → 实现它的调用通道，按声明顺序（1、2、2.5）。
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
