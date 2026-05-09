"""视频生成服务层公共 API。"""

from lib.providers import PROVIDER_ARK, PROVIDER_GEMINI, PROVIDER_GROK, PROVIDER_NEWAPI, PROVIDER_OPENAI
from lib.video_backends.base import (
    VideoBackend,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.video_backends.registry import create_backend, get_registered_backends, register_backend

__all__ = [
    "PROVIDER_ARK",
    "PROVIDER_GEMINI",
    "PROVIDER_GROK",
    "PROVIDER_NEWAPI",
    "PROVIDER_OPENAI",
    "VideoBackend",
    "VideoCapability",
    "VideoGenerationRequest",
    "VideoGenerationResult",
    "create_backend",
    "get_registered_backends",
    "register_backend",
]

# Auto-register backends
# Gemini: google-genai is a core dependency, import failure is a real error
from lib.video_backends.gemini import GeminiVideoBackend

register_backend(PROVIDER_GEMINI, GeminiVideoBackend)

# Ark: volcengine-python-sdk[ark] is a project dependency
from lib.video_backends.ark import ArkVideoBackend

register_backend(PROVIDER_ARK, ArkVideoBackend)

# Grok: xai-sdk
from lib.video_backends.grok import GrokVideoBackend

register_backend(PROVIDER_GROK, GrokVideoBackend)

# OpenAI Sora
from lib.video_backends.openai import OpenAIVideoBackend

register_backend(PROVIDER_OPENAI, OpenAIVideoBackend)

# NewAPI 统一视频端点
from lib.video_backends.newapi import NewAPIVideoBackend

register_backend(PROVIDER_NEWAPI, NewAPIVideoBackend)

# fork: Vidu — 单独 import 以避免与上游聚合 import 冲突
from lib.providers import PROVIDER_VIDU  # noqa: E402
from lib.video_backends.vidu import ViduVideoBackend  # noqa: E402

register_backend(PROVIDER_VIDU, ViduVideoBackend)
