"""语音合成（TTS）服务层公共 API。"""

from lib.audio_backends.base import (
    AudioBackend,
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    VoiceOption,
)
from lib.audio_backends.registry import create_backend, get_registered_backends, register_backend

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
from lib.audio_backends.dashscope import DashScopeAudioBackend
from lib.providers import PROVIDER_DASHSCOPE

register_backend(PROVIDER_DASHSCOPE, DashScopeAudioBackend)

from lib.audio_backends.croco import CrocoAudioBackend
from lib.providers import PROVIDER_CROCO

register_backend(PROVIDER_CROCO, CrocoAudioBackend)

from lib.audio_backends.doubao import DoubaoAudioBackend
from lib.providers import PROVIDER_DOUBAO

register_backend(PROVIDER_DOUBAO, DoubaoAudioBackend)
