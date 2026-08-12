"""语音合成（TTS）服务层公共 API。"""

from lib.audio_backends.base import (
    AudioBackend,
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    VoiceOption,
)
from lib.audio_backends.minimax import (
    MiniMaxVoiceDesignClient,
    VoiceDesignRequest,
    VoiceDesignResult,
    extract_voice_design_id,
)
from lib.audio_backends.registry import create_backend, get_registered_backends, register_backend

__all__ = [
    "AudioBackend",
    "AudioCapability",
    "AudioSynthesisRequest",
    "AudioSynthesisResult",
    "MiniMaxVoiceDesignClient",
    "VoiceOption",
    "VoiceDesignRequest",
    "VoiceDesignResult",
    "create_backend",
    "extract_voice_design_id",
    "get_registered_backends",
    "register_backend",
]

# Backend auto-registration
from lib.audio_backends.dashscope import DashScopeAudioBackend
from lib.providers import PROVIDER_DASHSCOPE

register_backend(PROVIDER_DASHSCOPE, DashScopeAudioBackend)
