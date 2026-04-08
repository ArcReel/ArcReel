"""ArkTextBackend — Volcano Engine Ark text generation backend."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from lib.ark_shared import ARK_BASE_URL, create_ark_client, resolve_ark_api_key
from lib.providers import PROVIDER_ARK
from lib.retry import with_retry_async
from lib.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "doubao-seed-2-0-lite-260215"


class ArkTextBackend:
    """Ark (Volcano Engine) text generation backend."""

    def __init__(self, *, api_key: str | None = None, model: str | None = None):
        self._client = create_ark_client(api_key=api_key)
        # Instructor requires an openai.OpenAI instance; the Ark SDK client type is incompatible,
        # but the Ark API is OpenAI-compatible, so we create an additional native OpenAI client as a fallback.
        from openai import OpenAI

        self._openai_client = OpenAI(base_url=ARK_BASE_URL, api_key=resolve_ark_api_key(api_key))
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[TextCapability] = self._resolve_capabilities()

    def _resolve_capabilities(self) -> set[TextCapability]:
        """Build the capability set from model declarations in PROVIDER_REGISTRY."""
        from lib.config.registry import PROVIDER_REGISTRY

        base = {TextCapability.TEXT_GENERATION, TextCapability.VISION}
        provider_meta = PROVIDER_REGISTRY.get("ark")
        if provider_meta:
            model_info = provider_meta.models.get(self._model)
            if model_info and TextCapability.STRUCTURED_OUTPUT in model_info.capabilities:
                base.add(TextCapability.STRUCTURED_OUTPUT)
        # Unregistered models do not get STRUCTURED_OUTPUT: prefer Instructor fallback over calling a native API that would error
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

    @with_retry_async()
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
        messages = self._build_messages(request)

        if TextCapability.STRUCTURED_OUTPUT in self._capabilities:
            from lib.text_backends.base import resolve_schema

            schema = resolve_schema(request.response_schema)
            try:
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
            except Exception as exc:
                logger.warning("Native response_format failed (%s), falling back to Instructor/json_object path", exc)

        return await self._structured_fallback(request, messages)

    async def _structured_fallback(self, request: TextGenerationRequest, messages: list[dict]) -> TextGenerationResult:
        """Instructor / json_object 降级路径。"""
        from lib.text_backends.instructor_support import instructor_fallback_sync

        return await asyncio.to_thread(
            instructor_fallback_sync,
            client=self._openai_client,
            model=self._model,
            messages=messages,
            response_schema=request.response_schema,
            provider=PROVIDER_ARK,
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
