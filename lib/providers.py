"""Provider name constants, shared by image_backends and video_backends."""

from typing import Literal

PROVIDER_GEMINI = "gemini"
PROVIDER_ARK = "ark"
PROVIDER_GROK = "grok"
PROVIDER_OPENAI = "openai"

CallType = Literal["image", "video", "text"]
CALL_TYPE_IMAGE: CallType = "image"
CALL_TYPE_VIDEO: CallType = "video"
CALL_TYPE_TEXT: CallType = "text"
