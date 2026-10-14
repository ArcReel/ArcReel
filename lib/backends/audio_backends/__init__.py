"""语音合成（TTS）服务层公共 API。"""

from lib.backends.audio_backends.base import (
    AudioBackend,
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    VoiceOption,
)
from lib.backends.audio_backends.registry import create_backend, get_registered_backends, register_backend

__all__ = [
    "AudioBackend",
    "AudioCapability",
    "AudioSynthesisRequest",
    "AudioSynthesisResult",
    "VoiceOption",
    "create_backend",
    "get_registered_backends",
    "register_backend",
]

# Backend auto-registration
from lib.backends.audio_backends.dashscope import DashScopeAudioBackend
from lib.backends.audio_backends.gemini import GeminiAudioBackend
from lib.backends.audio_backends.minimax import MiniMaxAudioBackend
from lib.backends.providers import PROVIDER_DASHSCOPE, PROVIDER_GEMINI, PROVIDER_MINIMAX

register_backend(PROVIDER_DASHSCOPE, DashScopeAudioBackend)
register_backend(PROVIDER_MINIMAX, MiniMaxAudioBackend)
register_backend(PROVIDER_GEMINI, GeminiAudioBackend)
