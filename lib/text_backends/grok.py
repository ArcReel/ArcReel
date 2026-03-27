"""GrokTextBackend — xAI Grok 文本生成后端。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Set

from lib.providers import PROVIDER_GROK
from lib.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "grok-4-1-fast-reasoning"


class GrokTextBackend:
    """xAI Grok 文本生成后端。"""

    def __init__(self, *, api_key: Optional[str] = None, model: Optional[str] = None):
        if not api_key:
            raise ValueError("XAI_API_KEY 未设置")

        import xai_sdk
        self._xai_sdk = xai_sdk
        self._client = xai_sdk.Client(api_key=api_key)
        self._model = model or DEFAULT_MODEL
        self._capabilities: Set[TextCapability] = {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }

    @property
    def name(self) -> str:
        return PROVIDER_GROK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> Set[TextCapability]:
        return self._capabilities

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        chat = self._client.chat.create(model=self._model)

        # System prompt
        if request.system_prompt:
            chat.append(self._xai_sdk.chat.system(request.system_prompt))

        # Build user message parts
        user_parts: list = []

        # Images for vision
        if request.images:
            for img_input in request.images:
                if img_input.path:
                    from lib.image_backends.base import image_to_base64_data_uri
                    data_uri = image_to_base64_data_uri(img_input.path)
                    user_parts.append(self._xai_sdk.chat.image(image_url=data_uri))
                elif img_input.url:
                    user_parts.append(self._xai_sdk.chat.image(image_url=img_input.url))

        chat.append(self._xai_sdk.chat.user(request.prompt, *user_parts))

        # Structured output or plain
        if request.response_schema:
            DynamicModel = _schema_to_pydantic(request.response_schema)
            response, parsed = await asyncio.to_thread(chat.parse, DynamicModel)
            text = response.content if hasattr(response, "content") else parsed.model_dump_json()
        else:
            response = await asyncio.to_thread(chat.sample)
            text = response.content if hasattr(response, "content") else str(response)

        return TextGenerationResult(
            text=text.strip() if isinstance(text, str) else str(text),
            provider=PROVIDER_GROK,
            model=self._model,
        )


def _schema_to_pydantic(schema: dict):
    """Convert a JSON Schema dict to a dynamic Pydantic model."""
    from pydantic import create_model
    from typing import Any as AnyType

    properties = schema.get("properties", {})
    fields = {}
    for field_name, prop in properties.items():
        fields[field_name] = (AnyType, ...)

    return create_model("DynamicResponse", **fields)
