"""OpenAI Chat Completions 异步流式文本驱动。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import httpx
from openai import APIStatusError

from lib.backends.openai_shared import create_openai_client
from lib.backends.providers import PROVIDER_OPENAI
from lib.backends.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    TokenParam,
    chat_completion_result,
    check_truncation,
    resolve_schema,
)
from lib.backends.text_backends.streaming import CONNECT_TIMEOUT, NativeSchemaRejected, TextDelta, execute_stream
from lib.backends.transport_layer import TransportLayer
from lib.config.url_utils import is_official_openai_base_url

DEFAULT_MODEL = "gpt-5.4-mini"


class OpenAITextBackend:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
        provider_name: str = PROVIDER_OPENAI,
        strict: bool = True,
        vision: bool = True,
        token_param: TokenParam | None = None,
    ):
        self._client = create_openai_client(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            transport_layer=transport_layer,
            timeout=httpx.Timeout(None, connect=CONNECT_TIMEOUT, pool=CONNECT_TIMEOUT),
        )
        self._provider_name = provider_name
        self._model = model or DEFAULT_MODEL
        self._strict = strict
        self._vision = vision
        self._max_tokens_param = token_param or (
            "max_completion_tokens" if is_official_openai_base_url(base_url) else "max_tokens"
        )

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[TextCapability]:
        caps = {TextCapability.TEXT_GENERATION, TextCapability.STRUCTURED_OUTPUT}
        if self._vision:
            caps.add(TextCapability.VISION)
        return caps

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        return await execute_stream(
            request, self._stream, provider=self.name, model=self.model, protocol="openai_chat", strict=self._strict
        )

    async def _stream(self, request: TextGenerationRequest) -> AsyncGenerator[TextDelta | TextGenerationResult]:
        kwargs: dict = {
            "model": self.model,
            "messages": await asyncio.to_thread(_build_messages, request),
            "stream_options": {"include_usage": True},
        }
        if request.max_output_tokens is not None:
            kwargs[self._max_tokens_param] = request.max_output_tokens
        if request.response_schema is not None:
            schema = {"name": "response", "schema": resolve_schema(request.response_schema)}
            if self._strict:
                schema["strict"] = True
            kwargs["response_format"] = {"type": "json_schema", "json_schema": schema}

        while True:
            try:
                stream = await self._client.chat.completions.create(**kwargs, stream=True)
                break
            except APIStatusError as exc:
                param = (exc.param or "").partition(".")[0]
                if (
                    exc.status_code in {400, 422}
                    and "stream_options" in kwargs
                    and (param == "stream_options" or (param == "" and exc.code != "invalid_json_schema"))
                ):
                    del kwargs["stream_options"]
                    continue
                if request.response_schema is not None and (
                    (exc.status_code in {400, 422} and param in {"", "response_format"})
                    or (exc.status_code == 429 and param == "response_format")
                ):
                    raise NativeSchemaRejected() from None
                raise

        parts: list[str] = []
        finish_reason = None
        usage = None
        delivered = False
        try:
            async with stream:
                yield TextDelta("")
                async for chunk in stream:
                    if chunk.usage is not None:
                        usage = chunk.usage
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                    reasoning = getattr(choice.delta, "reasoning_content", None) or getattr(
                        choice.delta, "reasoning", None
                    )
                    if isinstance(reasoning, str) and reasoning:
                        yield TextDelta(reasoning, thinking=True)
                    content = getattr(choice.delta, "content", None)
                    if content:
                        parts.append(content)
                        yield TextDelta(content)
            if finish_reason is not None:
                result = chat_completion_result(text="".join(parts), provider=self.name, model=self.model, usage=usage)
                result.truncated = check_truncation(
                    finish_reason,
                    provider=self.name,
                    model=self.model,
                    output_tokens=result.output_tokens,
                    structured=False,
                )
                delivered = True
                yield result
        finally:
            if not delivered and usage is not None and request.on_usage:
                partial = chat_completion_result(text="", provider=self.name, model=self.model, usage=usage)
                for tokens in partial.request_tokens():
                    request.on_usage(tokens)


def _build_messages(request: TextGenerationRequest) -> list[dict]:
    """将 TextGenerationRequest 转为 OpenAI messages 格式。"""
    messages: list[dict] = []

    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})

    if request.images:
        from lib.backends.image_backends.base import image_to_base64_data_uri

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
