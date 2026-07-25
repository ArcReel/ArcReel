"""自定义供应商 Backend 包装类。

将已有后端（OpenAI/Gemini 等）包装为自定义供应商，覆盖 name 和 model 属性。
"""

from __future__ import annotations

from lib.audio_backends.base import AudioBackend, AudioCapability, AudioSynthesisRequest, AudioSynthesisResult
from lib.image_backends.base import ImageBackend, ImageCapability, ImageGenerationRequest, ImageGenerationResult
from lib.text_backends.base import TextBackend, TextCapability, TextGenerationRequest, TextGenerationResult
from lib.video_backends.base import (
    VideoBackend,
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)


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
    """自定义供应商图片生成后端包装类。"""

    def __init__(self, *, provider_id: str, delegate: ImageBackend, model: str) -> None:
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
    def capabilities(self) -> set[ImageCapability]:
        return self._delegate.capabilities

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        return await self._delegate.generate(request)


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
    ) -> None:
        self._provider_id = provider_id
        self._delegate = delegate
        self._model = model
        self._video_capabilities = video_capabilities

    def with_video_capabilities(self, capabilities: VideoCapabilities) -> CustomVideoBackend:
        """返回注入生效能力的新实例（不就地改写，包装器保持构造后不可变）。"""
        return CustomVideoBackend(
            provider_id=self._provider_id,
            delegate=self._delegate,
            model=self._model,
            video_capabilities=capabilities,
        )

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._delegate.capabilities

    @property
    def video_capabilities(self) -> VideoCapabilities:
        if self._video_capabilities is not None:
            return self._video_capabilities
        return self._delegate.video_capabilities

    def video_capabilities_for_tier(self, service_tier: str, resolution: str | None = None) -> VideoCapabilities:
        """按请求档位收窄能力，转发给被包装 backend（如 Kling）——与 `video_capabilities`
        同一优先级：注入生效能力（用户覆盖）存在时优先返回它，否则查被包装 backend 是否实现
        tier-aware 查询（`getattr` 探测，与 `media_generator` 的探测方式一致），未实现则回落
        `video_capabilities`。缺失本方法时 `media_generator` 会回落到无请求上下文的保守声明，
        让 Pro 档本可接受的尾帧被静默丢弃（`KlingVideoBackend.video_capabilities_for_tier` 的
        docstring 详述了该保守声明的由来）。
        """
        if self._video_capabilities is not None:
            return self._video_capabilities
        tier_aware = getattr(self._delegate, "video_capabilities_for_tier", None)
        if tier_aware is not None:
            return tier_aware(service_tier, resolution=resolution)
        return self._delegate.video_capabilities

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        return await self._delegate.generate(request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        # 透传到下游 backend；下游不支持 resume 时抛 NotImplementedError，
        # 由 orphan handler 标 [resume_unsupported]
        return await self._delegate.resume_video(job_id, request)
