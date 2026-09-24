"""GenerationContext —— 生成任务 provider 解析产物的单次收口入口（见 ``docs/adr/0049``）。

``resolve_generation_context`` 在单个 ConfigResolver session 内完成全部声明 lane 的解析与
backend 构造，返回不可变的 :class:`GenerationContext`（MediaGenerator + 各 lane 结果值对象）。
每条 lane 固定求解顺序：解析 ProviderModel → 经 ``assemble_backend``（``docs/adr/0039``）构造
backend → 按实际身份查 resolution 与视频请求事实（``docs/adr/0086``）。

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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from lib.backends.audio_backends.base import VoiceOption
from lib.backends.backend_assembly import assemble_backend
from lib.backends.gemini_shared import get_shared_rate_limiter
from lib.config.resolver import (
    ConfigResolver,
    VideoGenerationType,
    video_bucket_for_generation_mode,
)
from lib.custom_provider.backends import CustomVideoBackend
from lib.db.base import DEFAULT_USER_ID
from lib.generation.media_generator import MediaGenerator
from lib.generation.video_request_facts import (
    ExecutionVideoIdentity,
    VideoRequestFacts,
    VideoRequestFactsFailure,
    VideoRoute,
    evaluate_video_request_facts,
)
from lib.project.project_manager import get_project_manager

if TYPE_CHECKING:
    from lib.config.resolver import ProviderModel


rate_limiter = get_shared_rate_limiter()

_CacheKey = tuple[str, str, str | None, str | None]


class _BackendCache:
    """Backend 实例缓存：按 (media_type, provider_name, model, 任务类型桶) 复用实例，避免每次任务重建 API 客户端。

    桶进 key 是因为它参与构造：自定义供应商的默认模型按桶分槽，同一 (media_type, provider,
    model=None) 在 t2i 与 i2i 上装载出的是两个不同模型的 backend。

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
    generation_type: str | None = None,
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
            generation_type=generation_type,
        )

    return await _backend_cache.get_or_create((media_type, provider_name, effective_model, generation_type), _factory)


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
    generation_type: Literal["t2i", "i2i"] | None = None,
):
    """获取或创建 ImageBackend 实例（带缓存）。

    generation_type 是本次调用所属的任务类型桶：自定义供应商的默认模型按桶分槽，桶随构造一路
    传到 ``load_custom_backend``，也进缓存 key（t2i 与 i2i 不互相命中）。
    """
    return await _get_or_create_backend(
        "image", provider_name, provider_settings, resolver, default_image_model, generation_type
    )


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
    """声明当前任务需要 image lane。``generation_type`` 决定 t2i / i2i 默认槽（``docs/adr/0001``）。"""

    generation_type: Literal["t2i", "i2i"] = "t2i"


@dataclass(frozen=True)
class VideoLaneRequest:
    """声明当前任务需要 video lane。

    ``generation_type`` 决定 i2v / r2v 任务类型桶（``docs/adr/0054``）：图生视频 / 宫格 → i2v；
    参考生视频按视频单元解析后的实际参考图分流——有参考图 → r2v，无参考图的视频单元降级
    → i2v（由 executor 判定后声明，见 ``lib.script.reference_video.units``）。None = 不定桶，
    走旧三级解析且不过能力闸——供 resume 等按 payload 排空、不承诺能力的路径使用。

    ``route`` 声明时 lane 附带该路线的执行侧视频请求事实（``docs/adr/0086``），能力只经它读取；
    不定桶时按项目生成模式定桶求值。不声明路线的 lane（续跑按检查点排空）不求值能力。
    """

    generation_type: VideoGenerationType | None = None
    route: VideoRoute | None = None


@dataclass(frozen=True)
class AudioLaneRequest:
    """声明当前任务需要 audio lane（旁白配音）。"""


@dataclass(frozen=True)
class ImageLaneResult:
    """image lane 解析产物。

    ``provider_model`` 是规范 registry 身份；``backend_name`` / ``backend_model`` 是构造后
    backend 报告的实际身份——自定义供应商目标 model 被禁用回退时 ``backend_model`` 可能与
    ``provider_model.model_id`` 不同。``resolution`` 为 None 表示调用时不传 SDK 参数
    （``docs/adr/0019``）。``max_reference_images`` 是 backend 声明的参考图上限（0 = 不裁剪），
    编排层据此在渲染「图N」编号前裁剪参考图序列。与 video lane 的同名能力字段不同，它是
    backend 上的常量属性、不经能力查询，故无降级分支。
    """

    provider_model: ProviderModel
    backend_name: str
    backend_model: str
    resolution: str | None
    max_reference_images: int


@dataclass(frozen=True)
class VideoLaneResult:
    """video lane 解析产物。

    能力事实只在 ``request_facts``：以实际构造的 backend 身份求值的执行侧视频请求事实，解析不出
    时是带类型的失败、不降级；lane 请求未声明路线时为 None。``resolution`` 是按实际身份解析的
    请求分辨率，None 表示调用时不传该参数（``docs/adr/0019``），其余身份字段语义同
    :class:`ImageLaneResult`。
    """

    provider_model: ProviderModel
    backend_name: str
    backend_model: str
    resolution: str | None
    request_facts: VideoRequestFacts | VideoRequestFactsFailure | None = None
    # 未声明路线或事实求值失败时保留用户音频意图；成功事实直接给出该值。
    requested_generate_audio_fallback: bool = True
    # 自定义供应商解析出的 endpoint（ENDPOINT_REGISTRY 键）；内置供应商无该维度，为 None。
    # 续跑据此与提交时持久化的 endpoint 比对，见 server.services.tasks.resume_executor。
    endpoint: str | None = None

    @property
    def requested_generate_audio(self) -> bool:
        facts = self.request_facts
        return (
            facts.requested_generate_audio
            if isinstance(facts, VideoRequestFacts)
            else self.requested_generate_audio_fallback
        )

    @property
    def is_silent(self) -> bool:
        """这一集是否听不到声音——模型不产音（C 类）或本集关闭了音频，两条路径同口径。

        声音一致性取自视频请求事实，解析不出时按 "soft"（有信号才判定为真无声，见
        ``lib.config.resolver.derive_voice_consistency``）。声音特征描述随该判据一并不注入：
        它虽是提示词文本而非音频负载，但描述的是听得到的音色，无声成片里注入只会让模型把配额
        花在用不上的约束上。台词不看这一位——无声视频里台词文本照常下发，供应商可用作口型参考。
        参考生视频的同名判据见 ``lib.script.reference_video.voice_settings.VoiceRenderSettings.is_silent``。
        """
        facts = self.request_facts
        voice_consistency = facts.voice_consistency if isinstance(facts, VideoRequestFacts) else "soft"
        return voice_consistency == "none" or not self.requested_generate_audio


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
    的解析或构造失败即原样上抛、整次调用失败——无部分结果、无跨 provider 兜底；视频请求事实
    求值失败不抛出，以失败对象交给执行器按阶段处理。``project`` 是调用方已加载的项目快照；``project_path`` 可由已经
    持有项目路径的事务传入，避免同步事务解析当前配置时嵌套占用默认线程池。本函数不读项目。

    video lane 的定桶随 ``VideoLaneRequest.generation_type``：None 时按项目生成模式解析（见
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
            resolved = await r.resolve_image_backend(project, payload, generation_type=image.generation_type)
            image_backend = await _get_or_create_image_backend(
                resolved.provider_id,
                {},
                r,
                default_image_model=resolved.model_id or None,
                generation_type=image.generation_type,
            )
            image_result = ImageLaneResult(
                provider_model=resolved,
                backend_name=image_backend.name,
                backend_model=image_backend.model,
                resolution=await r.resolve_resolution(project, resolved.provider_id, image_backend.model),
                max_reference_images=int(image_backend.max_reference_images),
            )

        if video is not None:
            resolved = await r.resolve_video_backend(project, payload, generation_type=video.generation_type)
            video_backend = await _get_or_create_video_backend(
                resolved.provider_id,
                {},
                r,
                default_video_model=resolved.model_id or None,
            )
            actual_model = video_backend.model
            request_facts: VideoRequestFacts | VideoRequestFactsFailure | None = None
            if video.route is not None:
                # 带上该任务落的桶：音轨形态等逐路径能力位按执行子路径分叉，参考生视频内降级到
                # i2v 的镜头按 i2v 桶求值。
                request_facts = await evaluate_video_request_facts(
                    project,
                    route=video.route,
                    generation_type=video.generation_type
                    or video_bucket_for_generation_mode(project.get("generation_mode")),
                    identity=ExecutionVideoIdentity(resolved.provider_id, actual_model),
                    resolver=r,
                )
            video_result = VideoLaneResult(
                provider_model=resolved,
                backend_name=video_backend.name,
                backend_model=actual_model,
                resolution=await r.resolve_resolution(project, resolved.provider_id, actual_model),
                request_facts=request_facts,
                requested_generate_audio_fallback=(
                    await r.video_generate_audio_for_project(project)
                    if not isinstance(request_facts, VideoRequestFacts)
                    else True
                ),
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
