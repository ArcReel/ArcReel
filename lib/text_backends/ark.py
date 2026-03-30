"""ArkTextBackend — 火山方舟文本生成后端。"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from lib.providers import PROVIDER_ARK
from lib.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "doubao-seed-2-0-lite-260215"


class ArkTextBackend:
    """Ark (火山方舟) 文本生成后端。"""

    def __init__(self, *, api_key: str | None = None, model: str | None = None):
        from volcenginesdkarkruntime import Ark

        self._api_key = api_key or os.environ.get("ARK_API_KEY")
        if not self._api_key:
            raise ValueError("Ark API Key 未提供")

        self._client = Ark(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=self._api_key,
        )
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[TextCapability] = self._resolve_capabilities()

    def _resolve_capabilities(self) -> set[TextCapability]:
        """根据 PROVIDER_REGISTRY 中的模型声明构建能力集合。"""
        from lib.config.registry import PROVIDER_REGISTRY

        base = {TextCapability.TEXT_GENERATION, TextCapability.VISION}
        provider_meta = PROVIDER_REGISTRY.get("ark")
        if provider_meta:
            model_info = provider_meta.models.get(self._model)
            if model_info and TextCapability.STRUCTURED_OUTPUT in model_info.capabilities:
                base.add(TextCapability.STRUCTURED_OUTPUT)
        # 未注册模型不加 STRUCTURED_OUTPUT：宁可走 Instructor 降级也不调用会报错的原生 API
        return base

    @property
    def name(self) -> str:
        return PROVIDER_ARK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[TextCapability]:
        return self._capabilities

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        if request.images:
            return await self._generate_vision(request)
        if request.response_schema:
            return await self._generate_structured(request)
        return await self._generate_plain(request)

    async def _generate_plain(self, request: TextGenerationRequest) -> TextGenerationResult:
        messages = self._build_messages(request)
        response = await asyncio.to_thread(
            self._client.chat.completions.create,
            model=self._model,
            messages=messages,
        )
        return self._parse_chat_response(response)

    async def _generate_structured(self, request: TextGenerationRequest) -> TextGenerationResult:
        if TextCapability.STRUCTURED_OUTPUT in self._capabilities:
            from lib.text_backends.base import resolve_schema

            messages = self._build_messages(request)
            schema = resolve_schema(request.response_schema)
            response = await asyncio.to_thread(
                self._client.chat.completions.create,
                model=self._model,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "response",
                        "schema": schema,
                    },
                },
            )
            return self._parse_chat_response(response)
        else:
            if not isinstance(request.response_schema, type):
                raise TypeError(
                    f"Instructor 降级路径需要传入 Pydantic 模型类，收到 {type(request.response_schema).__name__}"
                )
            from lib.text_backends.instructor_support import generate_structured_via_instructor

            messages = self._build_messages(request)
            # Instructor 要求 openai.OpenAI 实例；Ark SDK client 类型不兼容，
            # 但 Ark API 是 OpenAI 兼容的，因此创建原生 OpenAI 客户端。
            from openai import OpenAI

            openai_client = OpenAI(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_key=self._api_key,
            )
            json_text, input_tokens, output_tokens = await asyncio.to_thread(
                generate_structured_via_instructor,
                client=openai_client,
                model=self._model,
                messages=messages,
                response_model=request.response_schema,
            )
            return TextGenerationResult(
                text=json_text,
                provider=PROVIDER_ARK,
                model=self._model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

    async def _generate_vision(self, request: TextGenerationRequest) -> TextGenerationResult:
        content: list[dict[str, Any]] = []
        for img in request.images or []:
            if img.path:
                from lib.image_backends.base import image_to_base64_data_uri

                data_uri = image_to_base64_data_uri(img.path)
                content.append({"type": "input_image", "image_url": data_uri})
            elif img.url:
                content.append({"type": "input_image", "image_url": img.url})

        content.append({"type": "input_text", "text": request.prompt})

        messages: list[dict] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": content})

        response = await asyncio.to_thread(
            self._client.responses.create,
            model=self._model,
            input=messages,
        )

        text = response.output_text if hasattr(response, "output_text") else str(response)
        input_tokens = getattr(getattr(response, "usage", None), "input_tokens", None)
        output_tokens = getattr(getattr(response, "usage", None), "output_tokens", None)

        return TextGenerationResult(
            text=text.strip() if isinstance(text, str) else str(text),
            provider=PROVIDER_ARK,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _build_messages(self, request: TextGenerationRequest) -> list[dict]:
        messages: list[dict] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        return messages

    def _parse_chat_response(self, response) -> TextGenerationResult:
        text = response.choices[0].message.content
        input_tokens = getattr(getattr(response, "usage", None), "prompt_tokens", None)
        output_tokens = getattr(getattr(response, "usage", None), "completion_tokens", None)
        return TextGenerationResult(
            text=text.strip() if isinstance(text, str) else str(text),
            provider=PROVIDER_ARK,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
