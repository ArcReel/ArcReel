"""视频生成服务层公共 API。"""

from lib.video_backends.base import (
    VideoBackend,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.video_backends.registry import create_backend, get_registered_backends, register_backend

__all__ = [
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
register_backend("gemini", GeminiVideoBackend)

# Seedance: volcengine SDK is optional, skip if not installed
try:
    from lib.video_backends.seedance import SeedanceVideoBackend
    register_backend("seedance", SeedanceVideoBackend)
except ImportError:
    pass
