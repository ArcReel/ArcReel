"""供应商名称常量，image_backends / video_backends 共用。"""

from typing import Literal

PROVIDER_GEMINI = "gemini"
PROVIDER_ARK = "ark"
PROVIDER_GROK = "grok"
PROVIDER_OPENAI = "openai"
PROVIDER_NEWAPI = "newapi"

CallType = Literal["image", "video", "text"]
CALL_TYPE_IMAGE: CallType = "image"
CALL_TYPE_VIDEO: CallType = "video"
CALL_TYPE_TEXT: CallType = "text"
