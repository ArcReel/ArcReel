"""图片生成服务层公共 API。"""

from lib.backends.image_backends.base import (
    ImageBackend,
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    ReferenceImage,
)
from lib.backends.image_backends.registry import create_backend, get_registered_backends, register_backend

__all__ = [
    "ImageBackend",
    "ImageCapability",
    "ImageCapabilityError",
    "ImageGenerationRequest",
    "ImageGenerationResult",
    "ReferenceImage",
    "create_backend",
    "get_registered_backends",
    "register_backend",
]
# Backend auto-registration
from lib.backends.image_backends.gemini import GeminiImageBackend
from lib.backends.providers import PROVIDER_ARK, PROVIDER_ARK_AGENT_PLAN, PROVIDER_GEMINI

register_backend(PROVIDER_GEMINI, GeminiImageBackend)

from lib.backends.image_backends.ark import ArkImageBackend

register_backend(PROVIDER_ARK, ArkImageBackend)
register_backend(PROVIDER_ARK_AGENT_PLAN, ArkImageBackend)

from lib.backends.image_backends.grok import GrokImageBackend
from lib.backends.providers import PROVIDER_GROK

register_backend(PROVIDER_GROK, GrokImageBackend)

from lib.backends.image_backends.openai import OpenAIImageBackend
from lib.backends.providers import PROVIDER_OPENAI

register_backend(PROVIDER_OPENAI, OpenAIImageBackend)

from lib.backends.image_backends.vidu import ViduImageBackend
from lib.backends.providers import PROVIDER_VIDU

register_backend(PROVIDER_VIDU, ViduImageBackend)

from lib.backends.image_backends.dashscope import DashScopeImageBackend
from lib.backends.providers import PROVIDER_DASHSCOPE

register_backend(PROVIDER_DASHSCOPE, DashScopeImageBackend)

from lib.backends.image_backends.minimax import MiniMaxImageBackend
from lib.backends.providers import PROVIDER_MINIMAX

register_backend(PROVIDER_MINIMAX, MiniMaxImageBackend)

from lib.backends.image_backends.kling import KlingImageBackend
from lib.backends.providers import PROVIDER_KLING

register_backend(PROVIDER_KLING, KlingImageBackend)

from lib.backends.image_backends.agnes import AgnesImageBackend
from lib.backends.providers import PROVIDER_AGNES

register_backend(PROVIDER_AGNES, AgnesImageBackend)
