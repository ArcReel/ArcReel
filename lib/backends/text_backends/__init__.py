"""文本生成服务层公共 API。"""

from lib.backends.text_backends.base import (
    TEXT_TASK_TIERS,
    VISION_REQUIRED_TASKS,
    ImageInput,
    TextBackend,
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    TextTaskTier,
    TextTaskType,
)
from lib.backends.text_backends.registry import create_backend, get_registered_backends, register_backend

__all__ = [
    "TEXT_TASK_TIERS",
    "VISION_REQUIRED_TASKS",
    "ImageInput",
    "TextBackend",
    "TextCapability",
    "TextGenerationRequest",
    "TextGenerationResult",
    "TextTaskTier",
    "TextTaskType",
    "create_backend",
    "get_registered_backends",
    "register_backend",
]

# Backend auto-registration
from lib.backends.providers import PROVIDER_GEMINI
from lib.backends.text_backends.gemini import GeminiTextBackend

register_backend(PROVIDER_GEMINI, GeminiTextBackend)

from lib.backends.providers import PROVIDER_ARK, PROVIDER_ARK_AGENT_PLAN
from lib.backends.text_backends.ark import ArkTextBackend

register_backend(PROVIDER_ARK, ArkTextBackend)
register_backend(PROVIDER_ARK_AGENT_PLAN, ArkTextBackend)

from lib.backends.providers import PROVIDER_GROK
from lib.backends.text_backends.grok import GrokTextBackend

register_backend(PROVIDER_GROK, GrokTextBackend)

from lib.backends.providers import PROVIDER_OPENAI
from lib.backends.text_backends.openai import OpenAITextBackend

register_backend(PROVIDER_OPENAI, OpenAITextBackend)

from lib.backends.providers import PROVIDER_AGNES
from lib.backends.text_backends.agnes import AgnesTextBackend

register_backend(PROVIDER_AGNES, AgnesTextBackend)
