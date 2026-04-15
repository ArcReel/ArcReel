"""文本生成服务层公共 API。"""

from lib.text_backends.base import (
    ImageInput,
    TextBackend,
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    TextTaskType,
)
from lib.text_backends.registry import create_backend, get_registered_backends, register_backend

__all__ = [
    "ImageInput",
    "TextBackend",
    "TextCapability",
    "TextGenerationRequest",
    "TextGenerationResult",
    "TextTaskType",
    "create_backend",
    "get_registered_backends",
    "register_backend",
]

# Backend auto-registration
from lib.providers import PROVIDER_GEMINI
from lib.text_backends.gemini import GeminiTextBackend

register_backend(PROVIDER_GEMINI, GeminiTextBackend)

from lib.providers import PROVIDER_ARK
from lib.text_backends.ark import ArkTextBackend

register_backend(PROVIDER_ARK, ArkTextBackend)

from lib.providers import PROVIDER_GROK
from lib.text_backends.grok import GrokTextBackend

register_backend(PROVIDER_GROK, GrokTextBackend)

from lib.providers import PROVIDER_OPENAI
from lib.text_backends.openai import OpenAITextBackend

register_backend(PROVIDER_OPENAI, OpenAITextBackend)

from lib.providers import PROVIDER_BAILIAN
from lib.text_backends.bailian import BailianTextBackend

register_backend(PROVIDER_BAILIAN, BailianTextBackend)
