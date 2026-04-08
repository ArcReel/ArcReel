"""Public API for the image generation service layer."""

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
from lib.image_backends.gemini import GeminiImageBackend
from lib.providers import PROVIDER_ARK, PROVIDER_GEMINI

register_backend(PROVIDER_GEMINI, GeminiImageBackend)

from lib.image_backends.ark import ArkImageBackend

register_backend(PROVIDER_ARK, ArkImageBackend)

from lib.image_backends.grok import GrokImageBackend
from lib.providers import PROVIDER_GROK

register_backend(PROVIDER_GROK, GrokImageBackend)

from lib.image_backends.openai import OpenAIImageBackend
from lib.providers import PROVIDER_OPENAI

register_backend(PROVIDER_OPENAI, OpenAIImageBackend)
