"""供应商名称常量，image_backends / video_backends 共用。"""

from typing import Literal

PROVIDER_GEMINI = "gemini"
PROVIDER_ARK = "ark"
PROVIDER_ARK_AGENT_PLAN = "ark-agent-plan"
PROVIDER_GROK = "grok"
PROVIDER_OPENAI = "openai"
PROVIDER_VIDU = "vidu"
PROVIDER_NEWAPI = "newapi"
PROVIDER_DASHSCOPE = "dashscope"
PROVIDER_MINIMAX = "minimax"
PROVIDER_KLING = "kling"
PROVIDER_ANTHROPIC = "anthropic"

CallType = Literal["image", "video", "text", "audio"]
CALL_TYPE_IMAGE: CallType = "image"
CALL_TYPE_VIDEO: CallType = "video"
CALL_TYPE_TEXT: CallType = "text"
CALL_TYPE_AUDIO: CallType = "audio"
