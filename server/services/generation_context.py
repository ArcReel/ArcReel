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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from lib.audio_backends.base import VoiceOption
from lib.backend_assembly import assemble_backend
from lib.config.resolver import ConfigResolver, VideoCapability, VoiceConsistency, get_provider_fallback
from lib.custom_provider.backends import CustomVideoBackend
from lib.db.base import DEFAULT_USER_ID
from lib.gemini_shared import get_shared_rate_limiter
from lib.media_generator import MediaGenerator
from lib.project_manager import get_project_manager

if TYPE_CHECKING:
    from lib.config.resolver import ProviderModel

logger = logging.getLogger(__name__)

rate_limiter = get_shared_rate_limiter()

_CacheKey = tuple[str, str, str | None]


class _BackendCache:
    """Backend 实例缓存：按 (media_type, provider_name, model) 复用实例，避免每次任务重建 API 客户端。

    缓存查询/构造/写回/失效在此单点实现，两条并发纪律藏在实现内、不扩大接口：

    - **代际 invariant**：``invalidate()`` 时代数 +1；代数须在等锁前（而非取得锁后）捕获，
      构造完成后代数未变才写回。代数已变——无论是本请求持锁构造期间发生失效，还是本请求在
      失效边界前排队等锁、失效后才拿到锁——该实例用完即弃，不写回缓存遮蔽新配置；该笔任务
      仍按入队时配置快照跑完。
    - **per-key single-flight**：同 key 并发 miss 经 per-key 锁串行化，只构造一次、各调用方
      拿到同一实例，避免并发构造出无人持有的多余 SDK client（全库无 backend 关闭协议）。
    """

    def __init__(self) -> None:
        self._entries: dict[_CacheKey, Any] = {}
        self._locks: dict[_CacheKey, asyncio.Lock] = {}
        self._generation = 0

    async def get_or_create(self, key: _CacheKey, factory: Callable[[], Awaitable[Any]]) -> Any:
        if key in self._entries:
            return self._entries[key]
        # 代数须在等锁前捕获：若在失效边界前排队等锁，即使失效后才拿到锁，也要按排队时的
        # 旧代数与失效后的当前代数不符处理，避免用旧 resolver 构造的实例污染新代际缓存。
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
    """清空 Backend 实例缓存。在供应商配置变更后调用。"""
    _backend_cache.invalidate()


async def _get_or_create_backend(
    media_type: str,
    provider_name: str,
    provider_settings: dict,
    resolver: ConfigResolver,
    default_model: str | None,
) -> Any:
    """组 key + 提供 factory closure，缓存纪律统一委托 :class:`_BackendCache`。"""
    effective_model = provider_settings.get("model") or default_model or None

    async def _factory() -> Any:
        return await assemble_backend(
            provider_id=provider_name,
            media_type=media_type,
            model_id=effective_model,
            resolver=resolver,
            rate_limiter=rate_limiter,
        )

    return await _backend_cache.get_or_create((media_type, provider_name, effective_model), _factory)


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
    return await _get_or_create_backend("video", provider_name, provider_settings, resolver, default_video_model)


async def _get_or_create_image_backend(
    provider_name: str,
    provider_settings: dict,
    resolver: ConfigResolver,
    *,
    default_image_model: str | None = None,
):
    """获取或创建 ImageBackend 实例（带缓存）。"""
    return await _get_or_create_backend("image", provider_name, provider_settings, resolver, default_image_model)


async def _get_or_create_audio_backend(
    provider_name: str,
    provider_settings: dict,
    resolver: ConfigResolver,
    *,
    default_audio_model: str | None = None,
):
    """获取或创建 AudioBackend 实例（带缓存）。audio 无媒体特例：自定义 + 简单族统一经构造缝。"""
    return await _get_or_create_backend("audio", provider_name, provider_settings, resolver, default_audio_model)


@dataclass(frozen=True)
class ImageLaneRequest:
    """声明当前任务需要 image lane。capability 决定 t2i / i2i 默认槽（``docs/adr/0001``）。"""

    capability: Literal["t2i", "i2i"] = "t2i"


@dataclass(frozen=True)
class VideoLaneRequest:
    """声明当前任务需要 video lane。

    ``capability`` 决定 i2v / r2v 任务类型桶（``docs/adr/0054``）：图生视频 / 宫格 → i2v；
    参考生视频按视频单元解析后的实际参考图分流——有参考图 → r2v，无参考图的视频单元降级
    → i2v（由 executor 判定后声明，见 ``lib.reference_video.units``）。None = 不定桶，
    走旧三级解析且不过能力闸——供 resume 等按 payload 排空、不承诺能力的路径使用。
    """

    capability: VideoCapability | None = None


@dataclass(frozen=True)
class AudioLaneRequest:
    """声明当前任务需要 audio lane（旁白配音）。"""


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
    供需要非空档位的调用方（参考生视频路径），其余语义同 :class:`ImageLaneResult`。
    """

    provider_model: ProviderModel
    backend_name: str
    backend_model: str
    resolution: str | None
    resolution_or_fallback: str
    supported_durations: tuple[int, ...]
    max_duration: int | None
    max_reference_images: int | None
    # 费用与实际 provider 出账口径的有声档位，直接来自 video capabilities。
    # 它与下方的 requested_generate_audio（用户开关意图）不等价。
    generate_audio: bool = False
    # 能力查询失败时降级为 "soft"（有信号才判定为真无声，与既有「无信号不落 none」口径一致，
    # 见 lib.config.resolver.derive_voice_consistency）。
    voice_consistency: VoiceConsistency = "soft"
    # 每请求可携带的参考音频段数上限。降级为 0 = 不绑定任何参考音频：绑定数超上限会被
    # gate_video_request 当场拒绝，能力不明时宁可退到 B 类软约束也不赌一个上限值。
    max_reference_audio_count: int = 0
    # 本集的无声开关（用户意图口径：全局设置 ← project.json 覆盖），与 MediaGenerator 结算时读的
    # video_generate_audio 同源，**不是** caps["generate_audio"] 那个叠加了恒含音出账与默认执行档
    # 判定的计价参数。为 False 时编排层不组装参考音频（台词文本照发）。与其余能力字段不同口径：
    # 它不依赖 provider 能力接口，独立解析（见 resolve_generation_context 内的调用），能力查询
    # 失败不会连带把它冲回默认值——冲回会静默重新允许参考音频上传，违背用户已关闭的意图。
    requested_generate_audio: bool = True
    # 音频是否须逐段挂在具体参考素材项上（如 wan2.7-r2v 的 reference_voice 字段）。为 True
    # 时渲染层派生的音频顺序（台词 speaker 首现顺序）不能假设与参考图顺序（mention 首现顺序）
    # 天然对齐，调用方须显式算出「谁的声音配哪张图」再随请求下发。能力查询失败降级为 False——
    # 与其余能力字段同口径，不明时不额外收紧。
    reference_audio_per_image: bool = False
    # 自定义供应商解析出的 endpoint（ENDPOINT_REGISTRY 键）；内置供应商无该维度，为 None。
    # 续跑据此与提交时持久化的 endpoint 比对，见 server.services.resume_executor。
    endpoint: str | None = None

    @property
    def is_silent(self) -> bool:
        """这一集是否听不到声音——模型不产音（C 类）或本集关闭了音频，两条路径同口径。

        声音特征描述随该判据一并不注入：它虽是提示词文本而非音频负载，但描述的是听得到的
        音色，无声成片里注入只会让模型把配额花在用不上的约束上。台词不看这一位——无声视频
        里台词文本照常下发，供应商可用作口型参考。参考生视频的同名判据见
        ``lib.reference_video.voice_settings.VoiceRenderSettings.is_silent``。
        """
        return self.voice_consistency == "none" or not self.requested_generate_audio


@dataclass(frozen=True)
class AudioLaneResult:
    """audio lane 解析产物。narration voice/speed、音色目录与 backend 解析在同一 session 内交付。

    ``voices`` 是该 backend 的音色枚举快照（值，非 backend 实例）：音色列表端点据此应答，
    合成任务据此校验请求音色，二者不必再触达 backend 对象。
    """

    provider_model: ProviderModel
    backend_name: str
    backend_model: str
    narration_voice: str
    narration_speed: float | None
    voices: tuple[VoiceOption, ...]


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
    project_path: Path | None = None,
    user_id: str = DEFAULT_USER_ID,
    image: ImageLaneRequest | None = None,
    video: VideoLaneRequest | None = None,
    audio: AudioLaneRequest | None = None,
) -> GenerationContext:
    """在单个 ConfigResolver session 内解析全部声明 lane、构造 backend 并组装 MediaGenerator。

    lane 传即声明、None 跳过，任务只为用到的 lane 付出配置要求与构造成本。任一声明 lane
    的解析或构造失败即原样上抛、整次调用失败——无部分结果、无跨 provider 兜底；仅能力
    查询失败降级空值放行。``project`` 是调用方已加载的项目快照；``project_path`` 可由已经
    持有项目路径的事务传入，避免同步事务解析当前配置时嵌套占用默认线程池。本函数不读项目。

    video lane 的定桶随 ``VideoLaneRequest.capability``：None 时按项目生成模式解析（见
    ``lib.config.resolver.caps_generation_mode``）——生成模式创建即定、整个项目按同一种模式生成，
    声音一致性等二维派生值因此不需要集号；显式给定时按指定桶解析（参考生视频内按视频单元分流的
    调用方自带判定结果）。
    """
    from lib.db import async_session_factory

    resolved_project_path = (
        project_path
        if project_path is not None
        else await asyncio.to_thread(get_project_manager().get_project_path, project_name)
    )
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
            resolved = await r.resolve_video_backend(project, payload, capability=video.capability)
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
            generate_audio = False
            voice_consistency: VoiceConsistency = "soft"
            max_reference_audio_count = 0
            reference_audio_per_image = False
            # 独立于能力解析：这是用户在 project.json / 全局设置里的无声意图，不来自 provider
            # 能力接口，能力解析失败不得连带把它冲回默认值 True（会静默重新允许参考音频上传）。
            requested_generate_audio = await r.video_generate_audio_for_project(project)
            try:
                caps = await r.video_capabilities_for_model(resolved.provider_id, actual_model, project)
                supported_durations = tuple(int(d) for d in caps.get("supported_durations") or [])
                max_duration = caps.get("max_duration")
                max_reference_images = caps.get("max_reference_images")
                generate_audio = bool(caps.get("generate_audio"))
                voice_consistency = caps.get("voice_consistency") or "soft"
                max_reference_audio_count = int(caps.get("max_reference_audio_count") or 0)
                reference_audio_per_image = bool(caps.get("reference_audio_per_image") or False)
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
                generate_audio=generate_audio,
                voice_consistency=voice_consistency,
                requested_generate_audio=requested_generate_audio,
                max_reference_audio_count=max_reference_audio_count,
                reference_audio_per_image=reference_audio_per_image,
                # 显式按类型分流而非 getattr 探测：endpoint 为 None 恰好是「跳过续跑比对」
                # 这条最宽松分支，属性一旦改名，探测式取值会静默失效且无任何信号。
                endpoint=video_backend.endpoint if isinstance(video_backend, CustomVideoBackend) else None,
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
                voices=tuple(audio_backend.list_voices()),
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
