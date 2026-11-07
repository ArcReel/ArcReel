"""Public API for video-generation services."""

from arcreel_market_core.video_backend_contract import (
    ReferenceAudioMode,
    VideoBackend,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.providers import (
    PROVIDER_ARK,
    PROVIDER_GEMINI,
    PROVIDER_GROK,
    PROVIDER_NEWAPI,
    PROVIDER_OPENAI,
)
from lib.backends.video_backends.registry import create_backend, get_registered_backends, register_backend

__all__ = [
    "PROVIDER_ARK",
    "PROVIDER_GEMINI",
    "PROVIDER_GROK",
    "PROVIDER_NEWAPI",
    "PROVIDER_OPENAI",
    "ReferenceAudioMode",
    "VideoBackend",
    "VideoGenerationRequest",
    "VideoGenerationResult",
    "create_backend",
    "get_registered_backends",
    "register_backend",
]

# Auto-register backends
# Gemini: google-genai is a core dependency, import failure is a real error
from lib.backends.video_backends.gemini import GeminiVideoBackend, VertexVideoBackend

register_backend(PROVIDER_GEMINI, GeminiVideoBackend)
register_backend("gemini-vertex-video", VertexVideoBackend)

# Gemini Omni uses Interactions, unlike Veo protocols; AI Studio/Vertex each have built-in endpoints.
from lib.backends.video_backends.gemini_interactions import (
    GeminiInteractionsVideoBackend,
    VertexInteractionsVideoBackend,
)

register_backend("gemini-interactions-video", GeminiInteractionsVideoBackend)
register_backend("gemini-vertex-interactions-video", VertexInteractionsVideoBackend)

# Ark: volcengine-python-sdk[ark] is a project dependency. Seedance generations register separate endpoints by name,
# shared by Ark/Ark Agent Plan, with credential differences in backend_assembly construction closures.
from lib.backends.video_backends.ark import SEEDANCE_BACKENDS

for _endpoint, _backend in SEEDANCE_BACKENDS.items():
    register_backend(_endpoint, _backend)

# Grok: xai-sdk
from lib.backends.video_backends.grok import GrokVideoBackend

register_backend(PROVIDER_GROK, GrokVideoBackend)

from lib.backends.video_backends.grok import GROK_BACKENDS

for _endpoint, _backend in GROK_BACKENDS.items():
    register_backend(_endpoint, _backend)

# OpenAI Sora
from lib.backends.video_backends.openai import OpenAIVideoBackend

register_backend(PROVIDER_OPENAI, OpenAIVideoBackend)

# fork: Vidu, separate imports avoid upstream aggregate-import conflicts.
from lib.backends.providers import PROVIDER_VIDU
from lib.backends.video_backends.vidu import ViduVideoBackend

register_backend(PROVIDER_VIDU, ViduVideoBackend)

from lib.backends.video_backends.vidu import VIDU_BACKENDS

for _endpoint, _backend in VIDU_BACKENDS.items():
    register_backend(_endpoint, _backend)

# Alibaba Bailian DashScope, HappyHorse / Wan videos.
from lib.backends.video_backends.dashscope import DASHSCOPE_VIDEO_BACKENDS

for _endpoint, _backend in DASHSCOPE_VIDEO_BACKENDS.items():
    register_backend(_endpoint, _backend)

# Kling direct JWT video, default kling-v2-5-turbo.
from lib.backends.providers import PROVIDER_KLING
from lib.backends.video_backends.kling import KlingVideoBackend

register_backend(PROVIDER_KLING, KlingVideoBackend)

from lib.backends.video_backends.kling import KLING_BACKENDS

for _endpoint, _backend in KLING_BACKENDS.items():
    register_backend(_endpoint, _backend)

# New Kling API: model names in paths, API Key only.
from lib.backends.video_backends.kling_model_path import KlingModelPathVideoBackend

register_backend(KlingModelPathVideoBackend.endpoint_key, KlingModelPathVideoBackend)

# Agnes apihub async video: bare base64, polling, resume.
from lib.backends.video_backends.agnes import AGNES_VIDEO_BACKENDS

for _endpoint, _backend in AGNES_VIDEO_BACKENDS.items():
    register_backend(_endpoint, _backend)
