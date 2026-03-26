"""图片生成服务层公共 API。"""
from lib.image_backends.base import (
    ImageBackend,
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ReferenceImage,
)
from lib.image_backends.registry import create_backend, get_registered_backends, register_backend

__all__ = [
    "ImageBackend",
    "ImageCapability",
    "ImageGenerationRequest",
    "ImageGenerationResult",
    "ReferenceImage",
    "create_backend",
    "get_registered_backends",
    "register_backend",
]
# Backend auto-registration
from lib.video_backends.base import PROVIDER_GEMINI
from lib.image_backends.gemini import GeminiImageBackend
register_backend(PROVIDER_GEMINI, GeminiImageBackend)
