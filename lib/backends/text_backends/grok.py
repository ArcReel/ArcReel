"""GrokTextBackend — xAI Grok 文本生成后端。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import asdict
from typing import cast

import httpx
from grpc import StatusCode
from grpc.aio import AioRpcError
from xai_sdk import chat as xai_chat
from xai_sdk.chat import Chunk, Response
from xai_sdk.proto import chat_pb2

from lib.backends.grok_shared import create_grok_client
from lib.backends.providers import PROVIDER_GROK
from lib.backends.text_backends.base import (
    RequestTokens,
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    check_truncation,
    resolve_schema,
)
from lib.backends.text_backends.streaming import CONNECT_TIMEOUT, NativeSchemaRejected, TextDelta, execute_stream

_TRUNCATION_REASONS = ("length", "REASON_MAX_LEN", "REASON_MAX_CONTEXT")

DEFAULT_MODEL = "grok-4.3"


class GrokTextBackend:
    """xAI Grok 文本生成后端。"""

    def __init__(
        self, *, api_key: str | None = None, model: str | None = None, connect_timeout: float = CONNECT_TIMEOUT
    ):
        self._client = create_grok_client(api_key=api_key, disable_retries=True)
        self._model = model or DEFAULT_MODEL
        self._connect_timeout = connect_timeout
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

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        return await execute_stream(request, self._stream, provider=self.name, protocol="grok", model=self.model)

    async def _stream(self, request: TextGenerationRequest) -> AsyncGenerator[TextDelta | TextGenerationResult]:
        chat_kwargs: dict = {"model": self._model}
        if request.max_output_tokens is not None:
            chat_kwargs["max_tokens"] = request.max_output_tokens
        if request.response_schema is not None:
            chat_kwargs["response_format"] = chat_pb2.ResponseFormat(
                format_type=chat_pb2.FORMAT_TYPE_JSON_SCHEMA,
                schema=json.dumps(resolve_schema(request.response_schema)),
            )
        chat = self._client.chat.create(**chat_kwargs)

        if request.system_prompt:
            chat.append(xai_chat.system(request.system_prompt))

        user_parts: list = []

        if request.images:
            for img_input in request.images:
                if img_input.path:
                    from lib.backends.image_backends.base import image_to_base64_data_uri

                    data_uri = await asyncio.to_thread(image_to_base64_data_uri, img_input.path)
                    user_parts.append(xai_chat.image(image_url=data_uri))
                elif img_input.url:
                    user_parts.append(xai_chat.image(image_url=img_input.url))

        chat.append(xai_chat.user(request.prompt, *user_parts))

        try:
            async with asyncio.timeout(self._connect_timeout):
                await self._client.wait_connected()
        except TimeoutError as exc:
            raise httpx.ConnectTimeout("Grok gRPC channel connect timeout") from exc

        pending = RequestTokens()
        response = None
        try:
            async with aclosing(cast(AsyncGenerator[tuple[Response, Chunk]], chat.stream())) as stream:
                async for snapshot, chunk in stream:
                    first = response is None
                    response = snapshot
                    if chunk.proto.HasField("usage"):
                        usage = response.usage
                        pending = RequestTokens(
                            input_tokens=usage.prompt_tokens,
                            output_tokens=usage.completion_tokens + usage.reasoning_tokens,
                            cache_read_tokens=usage.cached_prompt_text_tokens,
                        )
                    if first:
                        yield TextDelta("")
                    if chunk.content:
                        yield TextDelta(chunk.content)
                    if chunk.reasoning_content:
                        yield TextDelta(chunk.reasoning_content, thinking=True)
            if response is None or response.finish_reason == "REASON_INVALID":
                return
            if response.finish_reason not in _TRUNCATION_REASONS and response.finish_reason != "REASON_STOP":
                raise RuntimeError(f"Grok generation stopped ({response.finish_reason})")
            truncated = check_truncation(
                response.finish_reason,
                provider=self.name,
                model=self.model,
                output_tokens=pending.output_tokens,
                structured=False,
                truncation_values=_TRUNCATION_REASONS,
            )
            result = TextGenerationResult(
                text=response.content.strip(),
                provider=self.name,
                model=self.model,
                truncated=truncated,
                **asdict(pending),
            )
            pending = RequestTokens()
            yield result
        except AioRpcError as exc:
            if response is None and request.response_schema is not None and exc.code() == StatusCode.INVALID_ARGUMENT:
                raise NativeSchemaRejected() from exc
            raise
        finally:
            if request.on_usage and pending != RequestTokens():
                request.on_usage(pending)
