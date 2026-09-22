"""视频生成服务层公共 API。"""

from lib.backends.providers import (
    PROVIDER_ARK,
    PROVIDER_ARK_AGENT_PLAN,
    PROVIDER_GEMINI,
    PROVIDER_GROK,
    PROVIDER_NEWAPI,
    PROVIDER_OPENAI,
)
from lib.backends.video_backend_contract import (
    ReferenceAudioMode,
    VideoBackend,
    VideoGenerationRequest,
    VideoGenerationResult,
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
from lib.backends.video_backends.gemini import GeminiVideoBackend

register_backend(PROVIDER_GEMINI, GeminiVideoBackend)

# Ark: volcengine-python-sdk[ark] is a project dependency
from lib.backends.video_backends.ark import ArkVideoBackend

register_backend(PROVIDER_ARK, ArkVideoBackend)
register_backend(PROVIDER_ARK_AGENT_PLAN, ArkVideoBackend)

# Grok: xai-sdk
from lib.backends.video_backends.grok import GrokVideoBackend

register_backend(PROVIDER_GROK, GrokVideoBackend)

# OpenAI Sora
from lib.backends.video_backends.openai import OpenAIVideoBackend

register_backend(PROVIDER_OPENAI, OpenAIVideoBackend)

# fork: Vidu — 单独 import 以避免与上游聚合 import 冲突
from lib.backends.providers import PROVIDER_VIDU
from lib.backends.video_backends.vidu import ViduVideoBackend

register_backend(PROVIDER_VIDU, ViduVideoBackend)

# 阿里百炼 DashScope — HappyHorse / 万相视频
from lib.backends.providers import PROVIDER_DASHSCOPE
from lib.backends.video_backends.dashscope import DashScopeVideoBackend

register_backend(PROVIDER_DASHSCOPE, DashScopeVideoBackend)

# 可灵 Kling — JWT 直连视频（默认模型 kling-v2-5-turbo）
from lib.backends.providers import PROVIDER_KLING
from lib.backends.video_backends.kling import KlingVideoBackend

register_backend(PROVIDER_KLING, KlingVideoBackend)

# Agnes — apihub 异步视频端点（裸 base64 + 轮询 + resume）
from lib.backends.providers import PROVIDER_AGNES
from lib.backends.video_backends.agnes import AgnesVideoBackend

register_backend(PROVIDER_AGNES, AgnesVideoBackend)
