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

from lib.backends.audio_backends.ark_agent_plan import ArkAgentPlanAudioBackend
from lib.backends.audio_backends.dashscope import DashScopeAudioBackend
from lib.backends.audio_backends.dashscope_speech_synthesizer import DashScopeSpeechSynthesizerBackend
from lib.backends.audio_backends.gemini import GeminiAudioBackend
from lib.backends.audio_backends.grok import GrokAudioBackend
from lib.backends.audio_backends.minimax import MiniMaxAudioBackend
from lib.backends.providers import (
    PROVIDER_ARK_AGENT_PLAN,
    PROVIDER_DASHSCOPE,
    PROVIDER_GEMINI,
    PROVIDER_GROK,
    PROVIDER_MINIMAX,
)

register_backend(PROVIDER_DASHSCOPE, DashScopeAudioBackend)
register_backend(PROVIDER_MINIMAX, MiniMaxAudioBackend)
register_backend(PROVIDER_GEMINI, GeminiAudioBackend)
register_backend(PROVIDER_GROK, GrokAudioBackend)
register_backend(PROVIDER_ARK_AGENT_PLAN, ArkAgentPlanAudioBackend)

DASHSCOPE_AUDIO_BACKENDS = {
    "dashscope-tts": DashScopeAudioBackend,
    "dashscope-speech-synthesizer": DashScopeSpeechSynthesizerBackend,
}
for _endpoint, _backend in DASHSCOPE_AUDIO_BACKENDS.items():
    register_backend(_endpoint, _backend)
