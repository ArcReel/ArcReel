"""AnthropicTextBackend — Anthropic Messages 协议的文本生成后端。

经国内厂商与中转站的 Anthropic 协议入口调用。结构化输出走 Messages 原生的
``output_config.format``，不经 OpenAI 兼容层（后者忽略 ``response_format``）；图片走图片内容块。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import asdict
from typing import Any

import httpx
from anthropic import (
    AsyncAnthropic,
    BadRequestError,
    DefaultAsyncHttpxClient,
    UnprocessableEntityError,
    transform_schema,
)
from anthropic.lib.streaming._messages import accumulate_event
from anthropic.types import MessageDeltaUsage, Usage
from anthropic.types.parsed_message import ParsedMessage

from lib.backends.image_backends.base import image_to_base64_data_uri
from lib.backends.providers import PROVIDER_ANTHROPIC
from lib.backends.text_backends.base import (
    RequestTokens,
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    check_truncation,
    resolve_schema,
)
from lib.backends.text_backends.streaming import CONNECT_TIMEOUT, NativeSchemaRejected, TextDelta, execute_stream
from lib.backends.transport_layer import TransportLayer, layer_async_client
from lib.config.url_utils import OFFICIAL_ANTHROPIC_BASE_URL


class AnthropicTextBackend:
    """Anthropic Messages 文本生成后端，只服务自定义供应商的 ``anthropic-messages`` 端点。

    凭证只取自定义供应商自己的 api_key 与 base_url，与 Agent 的 Anthropic 凭证隔离（ADR 0027）。
    """

    def __init__(
        self, *, api_key: str, model: str, base_url: str | None = None, transport_layer: TransportLayer | None = None
    ):
        self._client = AsyncAnthropic(
            api_key=api_key,
            base_url=(base_url or "").strip() or OFFICIAL_ANTHROPIC_BASE_URL,
            max_retries=0,
            timeout=httpx.Timeout(None, connect=CONNECT_TIMEOUT, pool=CONNECT_TIMEOUT),
            http_client=(
                layer_async_client(DefaultAsyncHttpxClient(), transport_layer) if transport_layer is not None else None
            ),
        )
        self._model = model

    @property
    def name(self) -> str:
        return PROVIDER_ANTHROPIC

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[TextCapability]:
        return {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        return await execute_stream(
            request, self._stream, provider=self.name, protocol="anthropic_messages", model=self.model
        )

    async def _stream(self, request: TextGenerationRequest) -> AsyncGenerator[TextDelta | TextGenerationResult]:
        content = await asyncio.to_thread(_build_user_content, request)
        kwargs: dict = {
            "model": self._model,
            "max_tokens": request.max_output_tokens or 16384,
            "messages": [{"role": "user", "content": content}],
        }
        if request.system_prompt:
            kwargs["system"] = request.system_prompt
        if request.response_schema is not None:
            schema = resolve_schema(request.response_schema)
            try:
                native_schema = transform_schema(schema)
            except ValueError as exc:
                raise NativeSchemaRejected() from exc
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": native_schema}}

        try:
            stream = await self._client.messages.create(**kwargs, stream=True)
        except (BadRequestError, UnprocessableEntityError) as exc:
            if request.response_schema is not None:
                raise NativeSchemaRejected() from exc
            raise

        snapshot: ParsedMessage[Any] | None = None
        delivered = False
        try:
            async with stream:
                yield TextDelta("")
                async for event in stream:
                    if event.type == "message_start" and getattr(event.message, "usage", None) is None:
                        event.message.usage = Usage.model_construct()
                    if event.type == "message_delta" and getattr(event, "usage", None) is None:
                        event.usage = MessageDeltaUsage.model_construct(
                            output_tokens=getattr(snapshot.usage, "output_tokens", None) if snapshot else None
                        )
                    snapshot = accumulate_event(event=event, current_snapshot=snapshot)
                    if event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            yield TextDelta(event.delta.text)
                        elif event.delta.type == "thinking_delta":
                            yield TextDelta(event.delta.thinking, thinking=True)
                    elif event.type == "content_block_start" and event.content_block.type == "text":
                        yield TextDelta(event.content_block.text)
                    elif event.type == "message_stop":
                        tokens = _request_tokens(snapshot.usage)
                        result = TextGenerationResult(
                            text="".join(block.text for block in snapshot.content if block.type == "text").strip(),
                            provider=self.name,
                            model=self.model,
                            truncated=check_truncation(
                                snapshot.stop_reason,
                                provider=self.name,
                                model=self.model,
                                output_tokens=tokens.output_tokens,
                                structured=False,
                            ),
                            **asdict(tokens),
                        )
                        delivered = True
                        yield result
                        return
        finally:
            if not delivered and snapshot is not None and request.on_usage:
                tokens = _request_tokens(snapshot.usage)
                if tokens != RequestTokens():
                    request.on_usage(tokens)


def _request_tokens(usage: Usage) -> RequestTokens:
    cache_read = getattr(usage, "cache_read_input_tokens", None)
    cache_write = getattr(usage, "cache_creation_input_tokens", None)
    breakdown = getattr(usage, "cache_creation", None)
    uncached = getattr(usage, "input_tokens", None)
    return RequestTokens(
        input_tokens=None if uncached is None else uncached + (cache_read or 0) + (cache_write or 0),
        output_tokens=getattr(usage, "output_tokens", None),
        cache_read_tokens=cache_read,
        cache_write_5m_tokens=breakdown.ephemeral_5m_input_tokens if breakdown else cache_write,
        cache_write_1h_tokens=breakdown.ephemeral_1h_input_tokens if breakdown else None,
    )


def _build_user_content(request: TextGenerationRequest) -> list[dict]:
    """图片内容块在前、文本在后，组成 user 消息的 content。"""
    content: list[dict] = []
    for img in request.images or []:
        if img.path:
            header, data = image_to_base64_data_uri(img.path).split(",", 1)
            media_type = header.removeprefix("data:").split(";", 1)[0]
            content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
        elif img.url:
            content.append({"type": "image", "source": {"type": "url", "url": img.url}})
    content.append({"type": "text", "text": request.prompt})
    return content
