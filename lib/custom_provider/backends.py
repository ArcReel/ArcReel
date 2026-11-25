"""自定义供应商 Backend 包装类。

将已有后端（OpenAI/Gemini 等）包装为自定义供应商，覆盖 name 和 model 属性。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import copy
from dataclasses import replace
from typing import Literal, cast

from arcreel_catalog_core import CatalogEntry, VideoFacts
from arcreel_market_core.video_backend_contract import (
    VideoBackend,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.audio_backends.base import (
    AudioBackend,
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    VoiceOption,
)
from lib.backends.image_backends.base import (
    ImageBackend,
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
)
from lib.backends.provider_assets import InferenceCredentialRef, inference_credential, provider_asset_roles
from lib.backends.text_backends.base import TextBackend, TextCapability, TextGenerationRequest, TextGenerationResult
from lib.backends.video_backends.name_family import catalog_entry_of
from lib.backends.video_capabilities import effective_video_capabilities, projected_video_capabilities
from lib.billing.metering import Usage
from lib.capabilities.kernel import CapabilitySnapshot, RequestConditions
from lib.custom_provider.metering import custom_video_usage, with_image_counts
from lib.custom_provider.path_overrides import NO_PATH_OVERRIDES, PathOverrides, endpoint_guard, replaying_base_url


class CustomTextBackend:
    """自定义供应商文本生成后端包装类。"""

    def __init__(self, *, provider_id: str, delegate: TextBackend, model: str) -> None:
        self._provider_id = provider_id
        self._delegate = delegate
        self._model = model

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[TextCapability]:
        return self._delegate.capabilities

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        return await self._delegate.generate(request)


class CustomImageBackend:
    """自定义供应商图片生成后端包装类。

    ``capabilities`` 是调用通道声明（被包装 backend 实现了什么）。``model_capabilities`` 是模型事实一侧
    允许的部分（能力覆盖整维替换条目的值，``None`` 即不加约束），由工厂按模型行注入；执行层的能力校验
    取两者的交集（:attr:`effective_capabilities`），与任务类型桶的候选同一个结果。``entry_reference_limit``
    是对应条目记录的参考图上限（``None`` 为未收录，ComfyUI 端点不读条目），与通道上限两侧求值见
    :func:`lib.backends.image_capabilities.backend_reference_image_limit`。``quality_options`` 是两侧求值后的有效画质
    可选值（:func:`lib.backends.image_capabilities.custom_image_quality_options`），为空即没有画质选项。
    """

    def __init__(
        self,
        *,
        provider_id: str,
        delegate: ImageBackend,
        model: str,
        model_capabilities: frozenset[ImageCapability] | None = None,
        entry_reference_limit: int | None = None,
        reference_limit_override: int | None = None,
        quality_options: tuple[str, ...] = (),
        snapshot: CapabilitySnapshot | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._delegate = delegate
        self._model = model
        self._model_capabilities = model_capabilities
        self._entry_reference_limit = entry_reference_limit
        self.reference_limit_override = reference_limit_override
        self._quality_options = quality_options
        self._snapshot = snapshot

    def with_snapshot(self, snapshot: CapabilitySnapshot) -> CustomImageBackend:
        backend = copy(self)
        backend._snapshot = snapshot
        backend._quality_options = tuple(snapshot.effective.get("qualities", ()))
        return backend

    @property
    def snapshot(self) -> CapabilitySnapshot | None:
        return self._snapshot

    @property
    def quality_options(self) -> tuple[str, ...]:
        if self._snapshot is not None:
            return tuple(self._snapshot.effective.get("qualities", ()))
        return self._quality_options

    def with_model_facts(
        self,
        model_capabilities: frozenset[ImageCapability] | None,
        *,
        entry_reference_limit: int | None,
        reference_limit_override: int | None = None,
        quality_options: tuple[str, ...] = (),
    ) -> CustomImageBackend:
        """返回注入了模型事实一侧的新实例：文生图 / 图生图、参考图上限与有效画质可选值（工厂按模型行的条目与能力覆盖算出）。"""
        return CustomImageBackend(
            provider_id=self._provider_id,
            delegate=self._delegate,
            model=self._model,
            model_capabilities=model_capabilities,
            entry_reference_limit=entry_reference_limit,
            reference_limit_override=reference_limit_override,
            quality_options=quality_options,
        )

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._delegate.capabilities

    @property
    def model_capabilities(self) -> frozenset[ImageCapability] | None:
        return self._model_capabilities

    @property
    def entry_reference_limit(self) -> int | None:
        if self._snapshot is not None:
            value = self._snapshot.model_facts.get("max_reference_images")
            return value if isinstance(value, int) and not isinstance(value, bool) else None
        return self._entry_reference_limit

    @property
    def effective_capabilities(self) -> frozenset[ImageCapability]:
        """调用通道实现了、且模型事实一侧允许的文生图 / 图生图。"""
        if self._snapshot is not None:
            return frozenset(cap for cap in ImageCapability if self._snapshot.effective.get(cap.value) is True)
        implemented = frozenset(self.capabilities)
        return implemented if self._model_capabilities is None else implemented & self._model_capabilities

    async def reference_image_limit(self) -> int | None:
        return await self._delegate.reference_image_limit()

    async def remote_reference_image_limit(self) -> int | None:
        probe = getattr(self._delegate, "remote_reference_image_limit", None)
        if callable(probe):
            result = await cast(Callable[[], Awaitable[object]], probe)()
            return result if isinstance(result, int) and not isinstance(result, bool) else None
        return None

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        result = await self._delegate.generate(request)
        return replace(result, usage=with_image_counts(result.usage, len(request.reference_images)))


class CustomAudioBackend:
    """自定义供应商语音合成后端包装类。"""

    def __init__(self, *, provider_id: str, delegate: AudioBackend, model: str) -> None:
        self._provider_id = provider_id
        self._delegate = delegate
        self._model = model

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[AudioCapability]:
        return self._delegate.capabilities

    def list_voices(self) -> list[VoiceOption]:
        return self._delegate.list_voices()

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        return await self._delegate.synthesize(request)


class CustomVideoBackend:
    """自定义供应商视频生成后端包装类。

    ``video_capabilities`` 可被工厂注入生效能力（系统判定与模型条目两侧求值 ⊕ 用户覆盖），此时不再转发
    被包装 backend 的声明——后者只是通道一侧的来源，模型条目与用户覆盖都要能收窄或翻转执行层看到的能力。
    未注入时（endpoint 闭包直接构造）回落到转发。
    """

    def __init__(
        self,
        *,
        provider_id: str,
        delegate: VideoBackend,
        model: str,
        video_capabilities: VideoCapabilities | None = None,
        snapshot: CapabilitySnapshot | None = None,
        capability_overrides: dict[str, object] | None = None,
        entry_facts: VideoFacts | None = None,
        endpoint: str | None = None,
        path_overrides: PathOverrides = NO_PATH_OVERRIDES,
    ) -> None:
        self._provider_id = provider_id
        self._delegate = delegate
        self._model = model
        self._snapshot = snapshot
        self._video_capabilities = video_capabilities
        self._capability_overrides = capability_overrides or {}
        self._entry_facts = entry_facts
        self._endpoint = endpoint
        self._path_overrides = path_overrides

    @property
    def endpoint(self) -> str | None:
        """构造本 backend 所用的 endpoint（ENDPOINT_REGISTRY 键），由工厂注入。

        endpoint 决定协议，是 backend 构造的真正输入粒度；模型行的 endpoint 可在同媒体类型内
        被改写，故已提交任务的续跑须比对它而非只比对 provider/model（``docs/adr/0054``）。
        绕过工厂直接构造时为 None，此时续跑不比对、行为与未持久化 endpoint 的存量任务一致。
        """
        return self._endpoint

    @property
    def endpoint_guard(self) -> str | None:
        """冻结进执行检查点的协议标识：endpoint 连同构造所用的路径覆盖（``docs/adr/0101``）。

        续跑时覆盖已被修改，与「调用端点已更换」一样按协议漂移失败（``docs/adr/0007``）。
        """
        return None if self._endpoint is None else endpoint_guard(self._endpoint, self._path_overrides)

    def with_snapshot(self, snapshot: CapabilitySnapshot) -> CustomVideoBackend:
        backend = copy(self)
        backend._snapshot = snapshot
        backend._video_capabilities = projected_video_capabilities(snapshot)
        return backend

    def with_video_capabilities(
        self,
        capabilities: VideoCapabilities,
        *,
        overrides: dict[str, object] | None = None,
        entry_facts: VideoFacts | None = None,
    ) -> CustomVideoBackend:
        """返回注入生效能力的新实例（不就地改写，包装器保持构造后不可变）。

        ``overrides`` 是过滤后的稀疏用户覆盖（`filter_valid_overrides` 的返回值），``entry_facts`` 是模型对应条目
        上的视频事实（ComfyUI 端点不读条目，为 ``None``），两者供 `video_capabilities_for_tier` 在档位感知基底上
        按同一规则求值；与 ``capabilities``（两侧求值 ⊕ 覆盖的完整合成结果，供 context-free 的
        `video_capabilities` 属性使用）分开传递——两者不能合一，否则档位查询会短路回完整合成结果，见
        `video_capabilities_for_tier`。
        """
        return CustomVideoBackend(
            provider_id=self._provider_id,
            delegate=self._delegate,
            model=self._model,
            video_capabilities=capabilities,
            capability_overrides=overrides,
            entry_facts=entry_facts,
            endpoint=self._endpoint,
            path_overrides=self._path_overrides,
        )

    def with_endpoint(self, endpoint: str, *, path_overrides: PathOverrides = NO_PATH_OVERRIDES) -> CustomVideoBackend:
        """返回记住构造 endpoint 与路径覆盖的新实例（不就地改写，包装器保持构造后不可变）。"""
        return CustomVideoBackend(
            provider_id=self._provider_id,
            delegate=self._delegate,
            model=self._model,
            video_capabilities=self._video_capabilities,
            capability_overrides=self._capability_overrides,
            snapshot=self._snapshot,
            entry_facts=self._entry_facts,
            endpoint=endpoint,
            path_overrides=path_overrides,
        )

    def with_catalog_entry(self, entry: CatalogEntry | None) -> CustomVideoBackend:
        """返回让被包装协议 backend 按 ``entry`` 认定通道能力与素材引用槽位的新实例；协议不按条目认定时原样返回。

        ``entry`` 是这个模型对应的条目（条目绑定优先，否则按名字现查）。档位查询（:meth:`video_capabilities_for_tier`）
        以协议 backend 的声明为基底，所以协议 backend 要与工厂注入的合成能力按同一个条目认定；请求里的模型不变。
        素材引用槽位见 :attr:`provider_asset_roles`。
        """
        rebind = getattr(self._delegate, "with_catalog_entry", None)
        if not callable(rebind):
            return self
        delegate = cast("VideoBackend", rebind(entry))
        return CustomVideoBackend(
            provider_id=self._provider_id,
            delegate=delegate,
            model=self._model,
            video_capabilities=self._video_capabilities,
            capability_overrides=self._capability_overrides,
            snapshot=self._snapshot,
            entry_facts=self._entry_facts,
            endpoint=self._endpoint,
            path_overrides=self._path_overrides,
        )

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def video_capabilities(self) -> VideoCapabilities:
        if self._video_capabilities is not None:
            return self._video_capabilities
        return self._delegate.video_capabilities

    @property
    def inference_credential(self) -> InferenceCredentialRef | None:
        """被包装协议 backend 发请求所用的推理凭证，即这个自定义供应商的配置。"""
        return inference_credential(self._delegate)

    @property
    def provider_asset_roles(self) -> frozenset[str]:
        """沿用被包装协议 backend 的素材引用声明：能否发 ``asset://`` 取决于调用协议与模型条目，不随包装改变。

        工厂经 :meth:`with_catalog_entry` 交给协议 backend 这个模型对应的条目；没有交（直接构造）时协议 backend
        没有条目可认，不声明任何槽位。
        """
        return provider_asset_roles(self._delegate)

    def video_capabilities_for_tier(
        self,
        service_tier: str,
        resolution: str | None = None,
        *,
        bucket: Literal["i2v", "r2v"] = "i2v",
        inputs: frozenset[str] = frozenset(),
    ) -> VideoCapabilities:
        if self._snapshot is not None:
            conditions = RequestConditions(bucket, resolution, service_tier, inputs)
            return projected_video_capabilities(self._snapshot.for_request(conditions))
        tier_aware = getattr(self._delegate, "video_capabilities_for_tier", None)
        base = (
            tier_aware(service_tier, resolution=resolution)
            if tier_aware is not None
            else self._delegate.video_capabilities
        )
        if self._entry_facts is not None or self._capability_overrides:
            facts = self._entry_facts or VideoFacts(last_frame=True, reference_audio=True)
            return effective_video_capabilities(base, facts, self._capability_overrides)
        return base

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        if self._endpoint is not None:
            request = replace(request, execution_endpoint=self._endpoint)
        result = await self._delegate.generate(request)
        mode = (request.video_capabilities or self.video_capabilities).audio_track_for_route(
            "r2v" if request.reference_images else "i2v"
        )
        return result if mode.has_audio_track else replace(result, generate_audio=False)

    @property
    def catalog_entry(self) -> CatalogEntry | None:
        """被包装协议 backend 认定模型所按的条目（工厂经 :meth:`with_catalog_entry` 交给它）；没有交时为 ``None``。"""
        return catalog_entry_of(self._delegate)

    def metered_usage(
        self,
        model: str,
        request: VideoGenerationRequest,
        result: VideoGenerationResult | None = None,
        *,
        entry: CatalogEntry | None = None,
    ) -> Usage:
        """这次请求的计量：被包装的调用通道有声明时取它，认不得这个模型或没有声明时按通用口径上报。

        ``entry`` 是模型对应的条目，结算经 ``backend_metered_usage`` 调用时取 :attr:`catalog_entry`，被包装的调用通道
        按它认定模型。见 :mod:`lib.custom_provider.metering`。
        """
        if result is None and request.video_capabilities is None and self._video_capabilities is not None:
            request = replace(request, video_capabilities=self._video_capabilities)
        return custom_video_usage(getattr(self._delegate, "metered_usage", None), model, request, result, entry=entry)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        with replaying_base_url(request.submitted_base_url):
            result = await self._delegate.resume_video(job_id, request)
        if request.video_capabilities is None:
            return result
        mode = request.video_capabilities.audio_track_for_route("r2v" if request.reference_images else "i2v")
        return result if mode.has_audio_track else replace(result, generate_audio=False)
