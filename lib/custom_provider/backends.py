"""自定义供应商 Backend 包装类。

将已有后端（OpenAI/Gemini 等）包装为自定义供应商，覆盖 name 和 model 属性。
"""

from __future__ import annotations

from dataclasses import replace

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
from lib.backends.text_backends.base import TextBackend, TextCapability, TextGenerationRequest, TextGenerationResult
from lib.billing.metering import Usage
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
    取两者的交集（:attr:`effective_capabilities`），与任务类型桶的候选同一个结果。
    """

    def __init__(
        self,
        *,
        provider_id: str,
        delegate: ImageBackend,
        model: str,
        model_capabilities: frozenset[ImageCapability] | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._delegate = delegate
        self._model = model
        self._model_capabilities = model_capabilities

    def with_model_capabilities(self, model_capabilities: frozenset[ImageCapability] | None) -> CustomImageBackend:
        """返回注入了模型事实一侧能力的新实例（工厂按模型行的条目与能力覆盖算出）。"""
        return CustomImageBackend(
            provider_id=self._provider_id,
            delegate=self._delegate,
            model=self._model,
            model_capabilities=model_capabilities,
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
    def effective_capabilities(self) -> frozenset[ImageCapability]:
        """调用通道实现了、且模型事实一侧允许的文生图 / 图生图。"""
        implemented = frozenset(self.capabilities)
        return implemented if self._model_capabilities is None else implemented & self._model_capabilities

    async def reference_image_limit(self) -> int:
        # 自定义供应商的参考图截断发生在被包装的 backend 内，上限即它声明的那一个。
        return await self._delegate.reference_image_limit()

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        result = await self._delegate.generate(request)
        # 没有声明计量的通道（ComfyUI、声明式图片、OpenAI Chat 出图）按通用口径上报张数；内置调用通道自己带的
        # 计量原样保留并补上张数，计价交给对应条目的价目表（docs/adr/0100）。
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

    ``video_capabilities`` 可被工厂注入生效能力（系统判定 ⊕ 用户覆盖），此时不再转发被包装
    backend 的声明——后者只是系统判定的一个来源，用户覆盖必须能翻转执行层看到的能力。未注入
    时（endpoint 闭包直接构造）回落到转发。
    """

    def __init__(
        self,
        *,
        provider_id: str,
        delegate: VideoBackend,
        model: str,
        video_capabilities: VideoCapabilities | None = None,
        capability_overrides: dict[str, object] | None = None,
        endpoint: str | None = None,
        path_overrides: PathOverrides = NO_PATH_OVERRIDES,
    ) -> None:
        self._provider_id = provider_id
        self._delegate = delegate
        self._model = model
        self._video_capabilities = video_capabilities
        self._capability_overrides = capability_overrides or {}
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

    def with_video_capabilities(
        self, capabilities: VideoCapabilities, *, overrides: dict[str, object] | None = None
    ) -> CustomVideoBackend:
        """返回注入生效能力的新实例（不就地改写，包装器保持构造后不可变）。

        ``overrides`` 是过滤后的稀疏用户覆盖（`filter_valid_overrides` 的返回值），供
        `video_capabilities_for_tier` 叠加到档位感知基底上；与 ``capabilities``（系统判定 ⊕
        覆盖的完整合成结果，供 context-free 的 `video_capabilities` 属性使用）分开传递——
        两者不能合一，否则档位查询会短路回完整合成结果，见 `video_capabilities_for_tier`。
        """
        return CustomVideoBackend(
            provider_id=self._provider_id,
            delegate=self._delegate,
            model=self._model,
            video_capabilities=capabilities,
            capability_overrides=overrides,
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
            endpoint=endpoint,
            path_overrides=path_overrides,
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

    def video_capabilities_for_tier(self, service_tier: str, resolution: str | None = None) -> VideoCapabilities:
        """按请求档位收窄能力：以被包装 backend（如 Kling）的档位感知查询为基底——
        `getattr` 探测是否实现（与 `media_generator` 的探测方式一致），未实现则回落其
        context-free `video_capabilities`——再叠加稀疏用户覆盖（未覆盖字段跟随基底）。

        不能直接短路返回工厂注入的完整合成结果（`self._video_capabilities`）：那是用
        context-free 的系统判定算出的（对 Kling 等档位敏感 backend 而言是保守声明），工厂
        路径下该字段永远非 None，短路会让本方法在生产环境等价于未实现档位感知，
        Pro 档本可接受的尾帧被静默丢弃。
        """
        tier_aware = getattr(self._delegate, "video_capabilities_for_tier", None)
        base = (
            tier_aware(service_tier, resolution=resolution)
            if tier_aware is not None
            else self._delegate.video_capabilities
        )
        if self._capability_overrides:
            # 合并后不变式与 synthesize 同口径：本路径也是一次「基底 ⊕ 稀疏覆盖」合并，
            # 漏掉就会让档位查询产出 direct ⊕ 上限 0 这种合成侧已挡住的组合。
            from lib.custom_provider.capabilities import enforce_audio_capability_invariant, merge_overrides

            return enforce_audio_capability_invariant(
                merge_overrides(base, self._capability_overrides),
                endpoint=self._delegate.name,
                model_id=self._model,
            )
        return base

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        # 注入实际执行的 endpoint，让下游协议 backend 在 submit 后与 job_id 一并持久化——
        # 续跑据此判定协议是否已被换掉。replace 而非就地改写：request 由调用方持有，
        # 包装层不该留下副作用。
        if self._endpoint is not None:
            request = replace(request, execution_endpoint=self._endpoint)
        return await self._delegate.generate(request)

    def metered_usage(
        self, model: str, request: VideoGenerationRequest, result: VideoGenerationResult | None = None
    ) -> Usage:
        """这次请求的计量：被包装的调用通道有声明时取它，认不得这个模型或没有声明时按通用口径上报。

        见 :mod:`lib.custom_provider.metering`；结算经 ``backend_metered_usage`` 调用。
        """
        return custom_video_usage(getattr(self._delegate, "metered_usage", None), model, request, result)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        # 透传到下游 backend；下游不支持 resume 时抛 NotImplementedError，
        # 由 orphan handler 标 [resume_unsupported]。回放提交时的 base，让路径覆盖按它认出续跑请求。
        with replaying_base_url(request.submitted_base_url):
            return await self._delegate.resume_video(job_id, request)
