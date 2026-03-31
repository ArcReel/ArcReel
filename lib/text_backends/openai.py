"""OpenAITextBackend — OpenAI 文本生成后端。"""

from __future__ import annotations

import logging

from lib.openai_shared import create_openai_client
from lib.providers import PROVIDER_OPENAI
from lib.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    resolve_schema,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5.4-mini"


class OpenAITextBackend:
    """OpenAI 文本生成后端，支持 Chat Completions API。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self._client = create_openai_client(api_key=api_key, base_url=base_url)
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[TextCapability] = {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }

    @property
    def name(self) -> str:
        return PROVIDER_OPENAI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[TextCapability]:
        return self._capabilities

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        """生成文本回复。"""
        messages = _build_messages(request)
        kwargs: dict = {"model": self._model, "messages": messages}

        if request.response_schema:
            schema = resolve_schema(request.response_schema)
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": True,
                    "schema": schema,
                },
            }

        response = await self._client.chat.completions.create(**kwargs)

        usage = response.usage
        return TextGenerationResult(
            text=response.choices[0].message.content or "",
            provider=PROVIDER_OPENAI,
            model=self._model,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )


def _build_messages(request: TextGenerationRequest) -> list[dict]:
    """将 TextGenerationRequest 转为 OpenAI messages 格式。"""
    messages: list[dict] = []

    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})

    # 构建 user message
    if request.images:
        from lib.image_backends.base import image_to_base64_data_uri

        content: list[dict] = []
        for img in request.images:
            if img.path:
                data_uri = image_to_base64_data_uri(img.path)
                content.append({"type": "image_url", "image_url": {"url": data_uri}})
            elif img.url:
                content.append({"type": "image_url", "image_url": {"url": img.url}})
        content.append({"type": "text", "text": request.prompt})
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": request.prompt})

    return messages
