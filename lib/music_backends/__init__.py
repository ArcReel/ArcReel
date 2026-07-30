"""音乐生成服务层公共 API。"""

from lib.music_backends.base import (
    MusicBackend,
    MusicCapability,
    MusicCapabilityError,
    MusicGenerationRequest,
    MusicGenerationResult,
)
from lib.music_backends.registry import create_backend, get_registered_backends, register_backend

__all__ = [
    "MusicBackend",
    "MusicCapability",
    "MusicCapabilityError",
    "MusicGenerationRequest",
    "MusicGenerationResult",
    "create_backend",
    "get_registered_backends",
    "register_backend",
]

# Backend auto-registration
# MiniMax 海螺 — music-3.0 音乐生成（单步同步 /music_generation 端点）
from lib.music_backends.minimax import MiniMaxMusicBackend  # noqa: E402
from lib.providers import PROVIDER_MINIMAX  # noqa: E402

register_backend(PROVIDER_MINIMAX, MiniMaxMusicBackend)
