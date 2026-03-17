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
