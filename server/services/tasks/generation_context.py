"""生成任务的调用通道与执行事实（ADR 0049）。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.exc import SQLAlchemyError

from lib.backends.audio_backends.base import AudioCapability, VoiceOption
from lib.backends.gemini_shared import get_shared_rate_limiter
from lib.backends.image_capabilities import (
    effective_image_quality,
    resolved_image_reference_limit,
)
from lib.config.resolver import (
    ConfigResolver,
    ImageBucketCapabilityError,
    ProviderModel,
    VideoGenerationType,
    payload_video_pinned_pair,
    project_image_quality,
    project_resolution,
    video_bucket_for_generation_mode,
)
from lib.db.base import DEFAULT_USER_ID
from lib.generation.media_generator import MediaGenerator
from lib.generation.video_request_facts import (
    VideoRequestFacts,
    VideoRequestFactsError,
    VideoRequestFactsFailure,
    VideoRoute,
    route_failure_code,
    snapshot_video_request_facts,
)
from lib.project.project_manager import get_project_manager
from lib.prompts.reference_image_numbering import ReferenceImageSlot, require_reference_image_limit
from lib.video_runtime.assembly import assemble_video
from lib.video_runtime.audio import AudioRuntime, AudioTarget, assemble_audio
from lib.video_runtime.image_assembly import assemble_image
from lib.video_runtime.image_resolution import ImageRuntime, ImageTarget
from lib.video_runtime.resolution import VideoRuntime, VideoTarget

rate_limiter = get_shared_rate_limiter()

_CacheKey = tuple[str, str, str | None, str | None, str | None]


class _BackendCache:
    """Backend 实例缓存：按 (media_type, provider_name, model, 任务类型桶, 内置调用端点) 复用实例，避免每次任务重建 API 客户端。

    桶进 key 是因为它参与构造：自定义供应商的默认模型按桶分槽，同一 (media_type, provider,
    model=None) 在 t2i 与 i2i 上装载出的是两个不同模型的 backend。内置供应商的模型挂接的端点同样参与构造，进 key 后
    换了端点（设置页修改、官方目录同步收录）不会取到按旧端点构造的实例。

    缓存查询/构造/写回/失效在此单点实现，两条并发纪律藏在实现内、不扩大接口：

    - **代际 invariant**：``invalidate()`` 时代数 +1；代数须在等锁前（而非取得锁后）捕获，
      构造完成后代数未变才写回。代数已变——无论是本请求持锁构造期间发生失效，还是本请求在
      失效边界前排队等锁、失效后才拿到锁——该实例用完即弃，不写回缓存遮蔽新配置；该笔任务
      仍按入队时配置快照跑完。
    - **per-key single-flight**：同 key 并发 miss 经 per-key 锁串行化，只构造一次、各调用方
      拿到同一实例，避免并发构造出无人持有的多余 SDK client（全库无 backend 关闭协议）。
    """

    def __init__(self) -> None:
        self._entries: dict[_CacheKey, tuple[object, Any]] = {}
        self._locks: dict[_CacheKey, asyncio.Lock] = {}
        self._generation = 0

    def generation(self) -> int:
        return self._generation

    async def get_or_create(
        self,
        key: _CacheKey,
        factory: Callable[[], Awaitable[Any]],
        *,
        generation: int | None = None,
        spec: object = None,
    ) -> Any:
        if generation is None:
            generation = self._generation
        if generation != self._generation:
            return await factory()
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            entry = self._entries.get(key)
            if self._generation == generation and entry is not None and entry[0] == spec:
                return entry[1]
            backend = await factory()
            if self._generation == generation:
                self._entries[key] = (spec, backend)
            return backend

    def invalidate(self) -> None:
        self._generation += 1
        self._entries.clear()
        self._locks.clear()


_backend_cache = _BackendCache()


def invalidate_backend_cache() -> None:
    """清空 Backend 实例缓存。在供应商配置变更、官方目录同步装入新数据后调用。"""
    _backend_cache.invalidate()


def reset_backend_cache_for_tests() -> None:
    _backend_cache.invalidate()


async def _video_backend_for(target: VideoTarget):
    snapshot = target.snapshot
    return await _backend_cache.get_or_create(
        ("video", snapshot.provider_id, snapshot.model_id, None, None),
        lambda: assemble_video(target.spec),
        generation=target.generation,
        spec=target.spec,
    )


async def _image_backend_for(target: ImageTarget):
    snapshot = target.snapshot
    return await _backend_cache.get_or_create(
        ("image", snapshot.provider_id, snapshot.model_id, None, None),
        lambda: assemble_image(target.spec),
        generation=target.generation,
        spec=target.spec,
    )


async def _audio_backend_for_target(target: AudioTarget):
    return await _backend_cache.get_or_create(
        ("audio", target.provider_id, target.model_id, None, target.spec.endpoint),
        lambda: assemble_audio(target.spec),
        generation=target.generation,
        spec=target.spec,
    )


async def _audio_backend_for(provider_id: str, model_id: str, resolver: ConfigResolver):
    """构造（或取缓存的）指定音频 backend。供应商未配置或模型不可用时原样抛出构造错误。"""
    target = await AudioRuntime(resolver, capture_generation=_backend_cache.generation).for_identity(
        ProviderModel(provider_id, model_id)
    )
    return await _audio_backend_for_target(target)


@dataclass(frozen=True)
class AudioBackendProfile:
    """指定音频 backend 声明的能力与音色表（音色按 backend 给出的顺序）。"""

    capabilities: frozenset[AudioCapability]
    voices: tuple[VoiceOption, ...]


async def audio_backend_profile(provider_id: str, model_id: str) -> AudioBackendProfile:
    """指定音频 backend 的能力与音色表，供设置页判断配音语速是否生效、列出该模型所用端点的音色。"""
    from lib.db import async_session_factory

    backend = await _audio_backend_for(provider_id, model_id, ConfigResolver(async_session_factory))
    return AudioBackendProfile(capabilities=frozenset(backend.capabilities), voices=tuple(backend.list_voices()))


async def audio_backend_voice_ids(provider_id: str, model_id: str, resolver: ConfigResolver) -> tuple[str, ...]:
    """指定音频 backend 的音色 id，按 backend 给出的顺序，供新建项目预填与模型相符的音色。"""
    return tuple(v.id for v in (await _audio_backend_for(provider_id, model_id, resolver)).list_voices())


@dataclass(frozen=True)
class ImageLaneRequest:
    """声明当前任务需要 image lane。``generation_type`` 决定 t2i / i2i 默认槽（``docs/adr/0001``）。"""

    generation_type: Literal["t2i", "i2i"] = "t2i"
    references: tuple[ReferenceImageSlot, ...] = ()


@dataclass(frozen=True)
class VideoLaneRequest:
    """声明当前任务需要 video lane。

    ``generation_type`` 决定 i2v / r2v 任务类型桶（``docs/adr/0054``）：图生视频 / 宫格 → i2v；
    参考生视频按视频单元解析后的实际参考图分流——有参考图 → r2v，无参考图的视频单元降级
    → i2v（由 executor 判定后声明，见 ``lib.script.reference_video.units``）。两字段均为 None 时
    不定桶，走三级解析且不过能力闸，供按 payload 排空、不承诺能力的路径使用。

    ``route`` 声明时 lane 附带该路线的执行侧视频请求事实（``docs/adr/0086``），能力只经它读取；
    未显式定桶时按项目生成模式定桶解析身份与求值。不声明路线的 lane（续跑按检查点排空）不求值能力。

    """

    generation_type: VideoGenerationType | None = None
    route: VideoRoute | None = None
    uses_end_frame: bool = False
    service_tier: str = "default"


@dataclass(frozen=True)
class SubmittedVideoLaneRequest:
    identity: ProviderModel
    endpoint_guard: str | None
    job_id: str


@dataclass(frozen=True)
class AudioLaneRequest:
    """声明当前任务需要 audio lane（旁白配音）。"""


@dataclass(frozen=True)
class ImageLaneResult:
    """image lane 解析产物。

    ``provider_model`` 是规范 registry 身份；``backend_name`` / ``backend_model`` 是构造后
    backend 报告的实际身份——自定义供应商目标 model 被禁用回退时 ``backend_model`` 可能与
    ``provider_model.model_id`` 不同。``resolution`` 为 None 表示调用时不传 SDK 参数
    （``docs/adr/0019``）。``max_reference_images`` 是参考图上限生效值（条目与通道两侧求值，见
    ``lib.backends.image_capabilities.backend_reference_image_limit``）给出的数量上限。未收录时为 ``None``，不接受时为 0，渲染编号前按完整参考序列校验。``quality`` 是生效的画质（项目存值经
    ``lib.backends.image_capabilities.effective_image_quality`` 按有效画质可选值求得），``None`` 表示调用通道沿用
    按分辨率档位推出 ``quality`` 的既有做法。
    """

    provider_model: ProviderModel
    backend_name: str
    backend_model: str
    resolution: str | None
    max_reference_images: int | None
    quality: str | None = None


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
    endpoint: str | None = None

    @property
    def is_silent(self) -> bool:
        facts = self.request_facts
        if isinstance(facts, VideoRequestFactsFailure):
            raise VideoRequestFactsError(facts)
        if facts is None:
            raise RuntimeError("video lane has no request facts")
        return facts.voice_consistency == "none" or not facts.requested_generate_audio


@dataclass(frozen=True)
class AudioLaneResult:
    """audio lane 解析产物。音色目录与 backend 解析在同一 session 内交付。

    ``voices`` 是该 backend 的音色枚举快照（值，非 backend 实例）：音色列表端点据此应答，不必再触达
    backend 对象。它只是候选，认不出的型号为空；合成任务不按它校验请求音色。
    """

    provider_model: ProviderModel
    backend_name: str
    backend_model: str
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
    video: VideoLaneRequest | SubmittedVideoLaneRequest | None = None,
    audio: AudioLaneRequest | None = None,
) -> GenerationContext:
    """在单个 ConfigResolver session 内解析全部声明 lane、构造 backend 并组装 MediaGenerator。

    lane 传即声明、None 跳过，任务只为用到的 lane 付出配置要求与构造成本。任一声明 lane
    的解析或构造失败即原样上抛、整次调用失败——无部分结果、无跨 provider 兜底；视频请求事实
    求值失败以失败对象交给执行器按阶段处理。``project`` 是调用方已加载的项目快照；``project_path`` 可由已经
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
    image_provider_model: ProviderModel | None = None
    image_resolution: str | None = None
    image_quality: str | None = None
    video_result: VideoLaneResult | None = None
    audio_result: AudioLaneResult | None = None
    image_backend: Any = None
    video_backend: Any = None
    audio_backend: Any = None

    image_target: ImageTarget | None = None
    target: VideoTarget | None = None
    video_limits = None
    generation = _backend_cache.generation()
    async with resolver.session(snapshot_settings=isinstance(video, VideoLaneRequest)) as r:
        if image is not None:
            image_runtime = ImageRuntime(r, capture_generation=lambda: generation, rate_limiter=rate_limiter)
            try:
                image_target = await image_runtime.resolve(project, payload, bucket=image.generation_type)
            except ImageBucketCapabilityError as exc:
                if image.generation_type == "i2i":
                    require_reference_image_limit(image.references, 0, model=exc.model_id)
                raise
            image_snapshot = image_target.snapshot
            image_backend = await _image_backend_for(image_target)
            image_provider_model = ProviderModel(image_snapshot.provider_id, image_snapshot.model_id)
            image_resolution = (
                project_resolution(project, image_snapshot.provider_id, image_backend.model)
                or image_snapshot.default_resolution
            )
            image_quality = effective_image_quality(
                project_image_quality(project, image_snapshot.provider_id, image_backend.model),
                tuple(image_snapshot.effective["qualities"]),
            )

        if video is not None:
            runtime = VideoRuntime(r, capture_generation=lambda: generation, rate_limiter=rate_limiter)
            generation_type = None
            if isinstance(video, SubmittedVideoLaneRequest):
                target = await runtime.submitted(video.identity, video.endpoint_guard, job_id=video.job_id)
            else:
                generation_type = video.generation_type
                if generation_type is None and video.route is not None:
                    generation_type = video_bucket_for_generation_mode(project.get("generation_mode"))
                pinned = payload_video_pinned_pair(payload or {}, generation_type)
                if pinned:
                    target = await runtime.materialized(ProviderModel(*pinned[1]))
                elif generation_type:
                    target = await runtime.bucket(project, generation_type)
                else:
                    target = await runtime.default(project)
            snapshot = target.snapshot
            request_facts: VideoRequestFacts | VideoRequestFactsFailure | None = None
            if isinstance(video, VideoLaneRequest) and video.route is not None:
                assert generation_type is not None
                try:
                    requested_audio = await r.video_generate_audio_for_project(project)
                except SQLAlchemyError:
                    request_facts = VideoRequestFactsFailure(
                        route_failure_code(video.route, "unavailable"),
                        (
                            ("capability", generation_type),
                            ("provider", snapshot.provider_id),
                            ("model", snapshot.model_id),
                        ),
                    )
                else:
                    request_facts = snapshot_video_request_facts(
                        snapshot,
                        project,
                        route=video.route,
                        generation_type=generation_type,
                        uses_end_frame=video.uses_end_frame,
                        service_tier=video.service_tier,
                        requested_audio=requested_audio,
                    )
                video_limits = target.reference_payload_limits or await r.reference_payload_limits(snapshot.provider_id)
            video_backend = await _video_backend_for(target)
            video_result = VideoLaneResult(
                provider_model=ProviderModel(snapshot.provider_id, snapshot.model_id),
                backend_name=video_backend.name,
                backend_model=video_backend.model,
                resolution=request_facts.resolution if isinstance(request_facts, VideoRequestFacts) else None,
                request_facts=request_facts,
                endpoint=target.endpoint_guard,
            )

        if audio is not None:
            audio_target = await AudioRuntime(r, capture_generation=lambda: generation).resolve(project, payload)
            audio_backend = await _audio_backend_for_target(audio_target)
            audio_result = AudioLaneResult(
                provider_model=ProviderModel(audio_target.provider_id, audio_target.model_id),
                backend_name=audio_backend.name,
                backend_model=audio_backend.model,
                voices=tuple(audio_backend.list_voices()),
            )

    if image_target is not None and image_provider_model is not None:
        image_result = ImageLaneResult(
            provider_model=image_provider_model,
            backend_name=image_backend.name,
            backend_model=image_backend.model,
            resolution=image_resolution,
            max_reference_images=(await resolved_image_reference_limit(image_target.snapshot, image_backend)).to_json(),
            quality=image_quality,
        )

    generator = MediaGenerator(
        resolved_project_path,
        rate_limiter=rate_limiter,
        image_backend=image_backend,
        image_snapshot=image_target.snapshot if image_target else None,
        video_backend=video_backend,
        video_snapshot=target.snapshot if target else None,
        video_limits=video_limits,
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
