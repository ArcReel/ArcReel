"""视频生成服务层公共 API。"""

from lib.video_backends.base import (
    PROVIDER_GEMINI,
    PROVIDER_GROK,
    PROVIDER_SEEDANCE,
    VideoBackend,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.video_backends.registry import create_backend, get_registered_backends, register_backend

__all__ = [
    "PROVIDER_GEMINI",
    "PROVIDER_GROK",
    "PROVIDER_SEEDANCE",
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

# Seedance: volcengine-python-sdk[ark] is a project dependency
from lib.video_backends.seedance import SeedanceVideoBackend
register_backend(PROVIDER_SEEDANCE, SeedanceVideoBackend)

# Grok: xai-sdk
from lib.video_backends.grok import GrokVideoBackend
register_backend(PROVIDER_GROK, GrokVideoBackend)
