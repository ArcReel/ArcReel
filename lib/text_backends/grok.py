"""GrokTextBackend — xAI Grok 文本生成后端。"""

from __future__ import annotations

import logging

from xai_sdk import chat as xai_chat

from lib.grok_shared import create_grok_client
from lib.providers import PROVIDER_GROK
from lib.retry import with_retry_async
from lib.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "grok-4-1-fast-reasoning"


class GrokTextBackend:
    """xAI Grok 文本生成后端。"""

    def __init__(self, *, api_key: str | None = None, model: str | None = None):
        self._client = create_grok_client(api_key=api_key)
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[TextCapability] = {
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
    def capabilities(self) -> set[TextCapability]:
        return self._capabilities

    @with_retry_async(max_attempts=3, backoff_seconds=(2, 4, 8))
    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        chat = self._client.chat.create(model=self._model)

        # System prompt
        if request.system_prompt:
            chat.append(xai_chat.system(request.system_prompt))

        # Build user message parts
        user_parts: list = []

        # Images for vision
        if request.images:
            for img_input in request.images:
                if img_input.path:
                    from lib.image_backends.base import image_to_base64_data_uri

                    data_uri = image_to_base64_data_uri(img_input.path)
                    user_parts.append(xai_chat.image(image_url=data_uri))
                elif img_input.url:
                    user_parts.append(xai_chat.image(image_url=img_input.url))

        chat.append(xai_chat.user(request.prompt, *user_parts))

        # Structured output or plain
        if request.response_schema:
            if isinstance(request.response_schema, type):
                DynamicModel = request.response_schema
            else:
                from lib.text_backends.base import resolve_schema

                DynamicModel = _schema_to_pydantic(resolve_schema(request.response_schema))
            response, parsed = await chat.parse(DynamicModel)
            text = response.content if hasattr(response, "content") else parsed.model_dump_json()
        else:
            response = await chat.sample()
            text = response.content if hasattr(response, "content") else str(response)

        # Try to extract token usage from the response
        input_tokens = None
        output_tokens = None
        if hasattr(response, "usage"):
            usage = response.usage
            input_tokens = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None)
            output_tokens = getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None)

        return TextGenerationResult(
            text=text.strip() if isinstance(text, str) else str(text),
            provider=PROVIDER_GROK,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def _schema_to_pydantic(schema: dict):
    """Convert a JSON Schema dict to a dynamic Pydantic model.

    Maps basic JSON Schema types to Python types. Nested objects and arrays
    are mapped to dict/list respectively for flexibility.
    """
    from typing import Any as AnyType

    from pydantic import create_model

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields = {}

    _TYPE_MAP = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    for field_name, prop in properties.items():
        json_type = prop.get("type", "string")
        py_type = _TYPE_MAP.get(json_type, AnyType)

        if field_name in required:
            fields[field_name] = (py_type, ...)
        else:
            fields[field_name] = (py_type | None, None)

    return create_model("DynamicResponse", **fields)
