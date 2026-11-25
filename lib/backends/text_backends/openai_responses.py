"""OpenAIResponsesTextBackend — 经 OpenAI Responses API（``POST /v1/responses``）生成文本。

``o3-pro``、``gpt-5.5-pro``、``gpt-5.3-codex`` 等模型只开放 Responses，Chat Completions 调不了。
结构化输出编码为 ``text.format``（json_schema）；供应商拒收或未强制 schema 时降级到 prompt
注入路径，降级仍走 Responses，只支持 Responses 的模型也能完成。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import asdict

import httpx
from openai import BadRequestError, UnprocessableEntityError
from openai.types.responses import ResponseUsage

from lib.backends.openai_shared import create_openai_client
from lib.backends.providers import PROVIDER_OPENAI
from lib.backends.text_backends.base import (
    RequestTokens,
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    check_truncation,
    resolve_schema,
)
from lib.backends.text_backends.openai import DEFAULT_MODEL
from lib.backends.text_backends.streaming import CONNECT_TIMEOUT, NativeSchemaRejected, TextDelta, execute_stream
from lib.backends.transport_layer import TransportLayer


class OpenAIResponsesTextBackend:
    """OpenAI Responses 文本后端：原生 ``text.format`` 优先，不合规时降级到 prompt 注入。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
        provider_name: str = PROVIDER_OPENAI,
    ):
        self._client = create_openai_client(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            transport_layer=transport_layer,
            timeout=httpx.Timeout(None, connect=CONNECT_TIMEOUT, pool=CONNECT_TIMEOUT),
        )
        self._model = model or DEFAULT_MODEL
        self._provider_name = provider_name
        self._capabilities: set[TextCapability] = {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[TextCapability]:
        return self._capabilities

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        return await execute_stream(
            request, self._stream, provider=self.name, protocol="openai_responses", model=self.model
        )

    async def _stream(self, request: TextGenerationRequest) -> AsyncGenerator[TextDelta | TextGenerationResult]:
        input_items = await asyncio.to_thread(_build_input, request)
        kwargs: dict = {"model": self._model, "input": input_items, "store": False}
        if request.system_prompt:
            kwargs["instructions"] = request.system_prompt
        if request.max_output_tokens is not None:
            kwargs["max_output_tokens"] = request.max_output_tokens
        if request.response_schema is not None:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "response",
                    "schema": resolve_schema(request.response_schema),
                }
            }

        try:
            stream = await self._client.responses.create(**kwargs, stream=True)
        except (BadRequestError, UnprocessableEntityError) as exc:
            if request.response_schema is not None and (
                exc.code == "invalid_json_schema"
                or (exc.param or "").startswith(("text.format", "response_format"))
                or (not exc.code and not exc.param)
            ):
                raise NativeSchemaRejected() from exc
            raise

        parts: list[str] = []
        pending = RequestTokens()
        refused = False
        try:
            async with stream:
                yield TextDelta("")
                async for event in stream:
                    if event.type == "response.output_text.delta":
                        parts.append(event.delta)
                        yield TextDelta(event.delta)
                    elif (
                        event.type == "response.reasoning_text.delta"
                        or event.type == "response.reasoning_summary_text.delta"
                    ):
                        yield TextDelta(event.delta, thinking=True)
                    elif event.type in ("response.refusal.delta", "response.refusal.done"):
                        refused = True
                    elif event.type == "error":
                        raise RuntimeError("Responses stream failed")
                    elif event.type == "response.created" or event.type == "response.in_progress":
                        if event.response.usage is not None:
                            pending = _tokens(event.response.usage)
                    elif (
                        event.type == "response.completed"
                        or event.type == "response.incomplete"
                        or event.type == "response.failed"
                    ):
                        response = event.response
                        tokens = _tokens(response.usage)
                        if response.usage is not None:
                            pending = tokens
                        if event.type != f"response.{response.status}" or response.status == "failed":
                            raise RuntimeError("Responses generation failed")
                        reason = (
                            getattr(response.incomplete_details, "reason", None)
                            if response.status == "incomplete"
                            else None
                        )
                        if response.status == "incomplete" and reason != "max_output_tokens":
                            raise RuntimeError("Responses generation incomplete")
                        if refused or any(
                            part.type == "refusal"
                            for item in response.output
                            if item.type == "message"
                            for part in item.content
                        ):
                            raise RuntimeError("Responses generation refused")
                        truncated = check_truncation(
                            reason,
                            provider=self.name,
                            model=self.model,
                            output_tokens=tokens.output_tokens,
                            structured=False,
                            truncation_values=("max_output_tokens",),
                        )
                        result = TextGenerationResult(
                            text="".join(parts) if parts else response.output_text,
                            provider=self.name,
                            model=self.model,
                            truncated=truncated,
                            **asdict(tokens),
                        )
                        pending = RequestTokens()
                        yield result
                        return
        finally:
            if request.on_usage and pending != RequestTokens():
                request.on_usage(pending)


def _tokens(usage: ResponseUsage | None) -> RequestTokens:
    if usage is None:
        return RequestTokens()
    return RequestTokens(
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        cache_read_tokens=getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", None),
    )


def _build_input(request: TextGenerationRequest) -> list[dict]:
    """将 TextGenerationRequest 转为 Responses 的 input 列表；system_prompt 走 instructions。"""
    if not request.images:
        return [{"role": "user", "content": request.prompt}]

    from lib.backends.image_backends.base import image_to_base64_data_uri

    content: list[dict] = []
    for img in request.images:
        if img.path:
            content.append({"type": "input_image", "image_url": image_to_base64_data_uri(img.path)})
        elif img.url:
            content.append({"type": "input_image", "image_url": img.url})
    content.append({"type": "input_text", "text": request.prompt})
    return [{"role": "user", "content": content}]
