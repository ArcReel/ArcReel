"""Custom provider Backend wrapper classes.

Wraps existing backends (OpenAI/Gemini, etc.) as custom providers, overriding the name and model properties.
"""

from __future__ import annotations

from lib.image_backends.base import ImageBackend, ImageCapability, ImageGenerationRequest, ImageGenerationResult
from lib.text_backends.base import TextBackend, TextCapability, TextGenerationRequest, TextGenerationResult
from lib.video_backends.base import VideoBackend, VideoCapability, VideoGenerationRequest, VideoGenerationResult


class CustomTextBackend:
    """Custom provider text generation backend wrapper."""

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
    """Custom provider image generation backend wrapper."""

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


class CustomVideoBackend:
    """Custom provider video generation backend wrapper."""

    def __init__(self, *, provider_id: str, delegate: VideoBackend, model: str) -> None:
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
    def capabilities(self) -> set[VideoCapability]:
        return self._delegate.capabilities

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        return await self._delegate.generate(request)
