"""GenerationContext —— 生成任务 provider 解析产物的单次收口入口（见 ``docs/adr/0049``）。

``resolve_generation_context`` 在单个 ConfigResolver session 内完成全部声明 lane 的解析与
backend 构造，返回不可变的 :class:`GenerationContext`（MediaGenerator + 各 lane 结果值对象）。
每条 lane 固定求解顺序：解析 ProviderModel → 经 ``assemble_backend``（``docs/adr/0039``）构造
backend → 按实际身份查 resolution 与能力。

查询身份 =（规范 registry provider_id, backend 实际 model）：provider 在构造缝中不可能漂移，
而族别名 provider（如 ark-agent-plan 复用 Ark backend）的 ``backend.name`` 是族名、非 registry
key，不能用作查询键；model 是唯一真实漂移轴（自定义供应商目标 model 被禁用时 loader 静默回退），
故取 backend 实际 ``.model``。lane 结果同时暴露 ``provider_model``（规范 registry 身份）与
``backend_name`` / ``backend_model``（backend 报告的实际身份）两组字段。

backend 实例缓存随本模块承载：缓存是 server 执行层关切（``docs/adr/0039``「缓存留在调用方」），
供应商配置变更路由经 ``invalidate_backend_cache()`` 统一失效。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from lib.backend_assembly import assemble_backend
from lib.config.resolver import ConfigResolver, get_provider_fallback
from lib.db.base import DEFAULT_USER_ID
from lib.gemini_shared import get_shared_rate_limiter
from lib.media_generator import MediaGenerator
from lib.project_manager import get_project_manager

if TYPE_CHECKING:
    from lib.config.resolver import ProviderModel

logger = logging.getLogger(__name__)

rate_limiter = get_shared_rate_limiter()

# 按 (channel, provider_name, model) 缓存 Backend 实例，避免每次任务重建 API 客户端
_backend_cache: dict[tuple[str, str, str | None], Any] = {}


def invalidate_backend_cache() -> None:
    """清空 Backend 实例缓存。在供应商配置变更后调用。"""
    _backend_cache.clear()


async def _get_or_create_video_backend(
    provider_name: str,
    provider_settings: dict,
    resolver: ConfigResolver,
    *,
    default_video_model: str | None = None,
):
    """获取或创建 VideoBackend 实例（带缓存）。

    provider_name 可以是旧格式（gemini/seedance/grok）或新格式（gemini-aistudio/gemini-vertex）。
    通过 resolver 按需加载供应商配置。
    default_video_model: 全局默认视频模型，当 provider_settings 中无 model 时作为 fallback。
    """
    effective_model = provider_settings.get("model") or default_video_model or None
    cache_key = ("video", provider_name, effective_model)
    if cache_key in _backend_cache:
        return _backend_cache[cache_key]

    backend = await assemble_backend(
        provider_id=provider_name,
        media_type="video",
        model_id=effective_model,
        resolver=resolver,
        rate_limiter=rate_limiter,
    )
    _backend_cache[cache_key] = backend
    return backend


async def _get_or_create_image_backend(
    provider_name: str,
    provider_settings: dict,
    resolver: ConfigResolver,
    *,
    default_image_model: str | None = None,
):
    """获取或创建 ImageBackend 实例（带缓存）。"""
    effective_model = provider_settings.get("model") or default_image_model or None
    cache_key = ("image", provider_name, effective_model)
    if cache_key in _backend_cache:
        return _backend_cache[cache_key]

    backend = await assemble_backend(
        provider_id=provider_name,
        media_type="image",
        model_id=effective_model,
        resolver=resolver,
        rate_limiter=rate_limiter,
    )
    _backend_cache[cache_key] = backend
    return backend


async def _get_or_create_audio_backend(
    provider_name: str,
    provider_settings: dict,
    resolver: ConfigResolver,
    *,
    default_audio_model: str | None = None,
):
    """获取或创建 AudioBackend 实例（带缓存）。"""
    effective_model = provider_settings.get("model") or default_audio_model or None
    cache_key = ("audio", provider_name, effective_model)
    if cache_key in _backend_cache:
        return _backend_cache[cache_key]

    # audio 无 gemini/kling 媒体特例：自定义 + 简单族统一经构造缝
    backend = await assemble_backend(
        provider_id=provider_name,
        media_type="audio",
        model_id=effective_model,
        resolver=resolver,
        rate_limiter=rate_limiter,
    )
    _backend_cache[cache_key] = backend
    return backend


@dataclass(frozen=True)
class ImageLaneRequest:
    """声明本次任务需要 image lane。capability 决定 t2i / i2i 默认槽（``docs/adr/0001``）。"""

    capability: Literal["t2i", "i2i"] = "t2i"


@dataclass(frozen=True)
class VideoLaneRequest:
    """声明本次任务需要 video lane。"""


@dataclass(frozen=True)
class AudioLaneRequest:
    """声明本次任务需要 audio lane（旁白 TTS）。"""


@dataclass(frozen=True)
class ImageLaneResult:
    """image lane 解析产物。

    ``provider_model`` 是规范 registry 身份；``backend_name`` / ``backend_model`` 是构造后
    backend 报告的实际身份——自定义供应商目标 model 被禁用回退时 ``backend_model`` 可能与
    ``provider_model.model_id`` 不同。``resolution`` 为 None 表示调用时不传 SDK 参数
    （``docs/adr/0019``）。
    """

    provider_model: ProviderModel
    backend_name: str
    backend_model: str
    resolution: str | None


@dataclass(frozen=True)
class VideoLaneResult:
    """video lane 解析产物。

    能力字段（``supported_durations`` / ``max_duration`` / ``max_reference_images``）在能力
    查询失败时降级为空值（空元组 / None）放行：能力是已选定 provider/model 的元数据，缺失
    不代表不可调用，守卫遇空值不施加限制、把决策推给 backend。``resolution_or_fallback``
    供需要非空档位的调用方（参考视频路径），其余语义同 :class:`ImageLaneResult`。
    """

    provider_model: ProviderModel
    backend_name: str
    backend_model: str
    resolution: str | None
    resolution_or_fallback: str
    supported_durations: tuple[int, ...]
    max_duration: int | None
    max_reference_images: int | None


@dataclass(frozen=True)
class AudioLaneResult:
    """audio lane 解析产物。narration voice/speed 与 backend 解析在同一 session 内交付。"""

    provider_model: ProviderModel
    backend_name: str
    backend_model: str
    narration_voice: str
    narration_speed: float | None


def _lane_not_declared(lane: str, request_hint: str) -> RuntimeError:
    return RuntimeError(
        f"{lane} lane 未声明：调用 resolve_generation_context 时传入 {request_hint} 才能访问该 lane 的解析产物"
    )


@dataclass(frozen=True)
class GenerationContext:
    """单次解析交付的全部产物：MediaGenerator + 各声明 lane 的结果值对象。

    lane 字段为 None 表示该 lane 未声明；经同名 property 访问未声明 lane 直接抛
    RuntimeError（fail-loud，返回类型非 Optional）。测试可用本 dataclass 直接拼装假 context。
    """

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


async def resolve_generation_context(
    project_name: str,
    payload: dict | None,
    *,
    project: dict,
    user_id: str = DEFAULT_USER_ID,
    image: ImageLaneRequest | None = None,
    video: VideoLaneRequest | None = None,
    audio: AudioLaneRequest | None = None,
) -> GenerationContext:
    """在单个 ConfigResolver session 内解析全部声明 lane、构造 backend 并组装 MediaGenerator。

    lane 传即声明、None 跳过，任务只为用到的 lane 付出配置要求与构造成本。任一声明 lane
    的解析或构造失败即原样上抛、整次调用失败——无部分结果、无跨 provider 兜底；仅能力
    查询失败降级空值放行。``project`` 是调用方已加载的项目快照，本函数不读盘。
    """
    from lib.db import async_session_factory

    project_path = await asyncio.to_thread(get_project_manager().get_project_path, project_name)
    resolver = ConfigResolver(async_session_factory)

    image_result: ImageLaneResult | None = None
    video_result: VideoLaneResult | None = None
    audio_result: AudioLaneResult | None = None
    image_backend: Any = None
    video_backend: Any = None
    audio_backend: Any = None

    async with resolver.session() as r:
        if image is not None:
            resolved = await r.resolve_image_backend(project, payload, capability=image.capability)
            image_backend = await _get_or_create_image_backend(
                resolved.provider_id,
                {},
                r,
                default_image_model=resolved.model_id or None,
            )
            image_result = ImageLaneResult(
                provider_model=resolved,
                backend_name=image_backend.name,
                backend_model=image_backend.model,
                resolution=await r.resolve_resolution(project, resolved.provider_id, image_backend.model),
            )

        if video is not None:
            resolved = await r.resolve_video_backend(project, payload)
            video_backend = await _get_or_create_video_backend(
                resolved.provider_id,
                {},
                r,
                default_video_model=resolved.model_id or None,
            )
            actual_model = video_backend.model
            resolution = await r.resolve_resolution(project, resolved.provider_id, actual_model)
            supported_durations: tuple[int, ...] = ()
            max_duration: int | None = None
            max_reference_images: int | None = None
            try:
                caps = await r.video_capabilities_for_model(resolved.provider_id, actual_model, project)
                supported_durations = tuple(int(d) for d in caps.get("supported_durations") or [])
                max_duration = caps.get("max_duration")
                max_reference_images = caps.get("max_reference_images")
            except Exception as exc:
                logger.info(
                    "无法解析 video capabilities（%s/%s），能力值降级为空：%s",
                    resolved.provider_id,
                    actual_model,
                    exc,
                )
            video_result = VideoLaneResult(
                provider_model=resolved,
                backend_name=video_backend.name,
                backend_model=actual_model,
                resolution=resolution,
                resolution_or_fallback=resolution or get_provider_fallback(resolved.provider_id),
                supported_durations=supported_durations,
                max_duration=max_duration,
                max_reference_images=max_reference_images,
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
                narration_voice=await r.resolve_narration_voice(project),
                narration_speed=await r.resolve_narration_speed(project),
            )

    generator = MediaGenerator(
        project_path,
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
