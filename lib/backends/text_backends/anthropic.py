"""AnthropicTextBackend: text generation over the Anthropic Messages protocol.

Call Anthropic-protocol endpoints from domestic vendors and relays. Structured output uses native Messages
``output_config.format``, bypassing OpenAI compatibility layers (which ignore ``response_format``); images use image content blocks.
"""

from __future__ import annotations

import asyncio
import logging

from anthropic import (
    APIConnectionError,
    APITimeoutError,
    AsyncAnthropic,
    BadRequestError,
    DefaultAsyncHttpxClient,
    InternalServerError,
    RateLimitError,
    transform_schema,
)

from lib.backends.image_backends.base import image_to_base64_data_uri
from lib.backends.providers import PROVIDER_ANTHROPIC
from lib.backends.text_backends.base import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    check_truncation,
    merge_billed_usage,
    prompt_json_fallback,
    resolve_schema,
    structured_fallback_reason,
    truncate_for_log,
)
from lib.backends.transport_layer import TransportLayer, layer_async_client
from lib.config.url_utils import OFFICIAL_ANTHROPIC_BASE_URL
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

ANTHROPIC_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)


class AnthropicTextBackend:
    """Anthropic Messages text backend, serving only custom-provider ``anthropic-messages`` endpoints.

    Credentials come exclusively from custom-provider api_key and base_url, isolated from Agent Anthropic credentials (ADR 0017).
    """

    def __init__(
        self, *, api_key: str, model: str, base_url: str | None = None, transport_layer: TransportLayer | None = None
    ):
        # Explicitly fill official base_url when empty, preventing SDK fallback to ANTHROPIC_BASE_URL environment settings.
        # Disable SDK retries; _generate_native manages them centrally.
        self._client = AsyncAnthropic(
            api_key=api_key,
            base_url=(base_url or "").strip() or OFFICIAL_ANTHROPIC_BASE_URL,
            max_retries=0,
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
        """Generate a text response.

        No retry decorator here: transient retries occur in :meth:`_generate_native`; validation/fallback
        stay outside retry scope, preventing fallback failures from replaying successful native calls.
        """
        # Build image requests in threads: local image reads/base64 encoding must not block the event loop.
        content = await asyncio.to_thread(_build_user_content, request)
        structured = bool(request.response_schema)
        native = await self._generate_native(request, content, structured=structured)

        if native is None:
            # Gateway rejects output_config (schema error): fall back for the entire structured-output attempt.
            return await self._prompt_json_fallback(request)

        if request.response_schema:
            # Relays may silently ignore output_config and return prose or invalid JSON. transform_schema moves
            # unsupported constraints into description; native requests effectively omit strict, so revalidate with strict=False.
            fallback_reason = structured_fallback_reason(native.text, request.response_schema, strict=False)
            if fallback_reason:
                logger.warning(
                    "原生 output_config %s，降级到 prompt 注入路径；模型原始输出：%s",
                    fallback_reason,
                    truncate_for_log(native.text),
                )
                result = await self._prompt_json_fallback(request)
                # This native 200 call was billed; merge its tokens into fallback results.
                merge_billed_usage(result, native)
                return result

        return native

    async def _prompt_json_fallback(self, request: TextGenerationRequest) -> TextGenerationResult:
        async def _once(fb_request: TextGenerationRequest) -> TextGenerationResult:
            content = await asyncio.to_thread(_build_user_content, fb_request)
            # Fallback omits output_config, so cannot return the schema-error fallback signal again.
            result = await self._generate_native(fb_request, content, structured=True)
            assert result is not None
            return result

        return await prompt_json_fallback(request, _once, provider=PROVIDER_ANTHROPIC, model=self._model)

    @with_retry_async(max_attempts=4, backoff_seconds=(2, 4, 8), retryable_errors=ANTHROPIC_RETRYABLE_ERRORS)
    async def _generate_native(
        self, request: TextGenerationRequest, content: list[dict], *, structured: bool
    ) -> TextGenerationResult | None:
        """One Messages call: retry transient errors; return ``None`` to signal fallback when output_config is rejected.

        Output limits can reach tens of thousands of tokens; SDK rejects nonstreaming calls for timeout risk, so always stream and retrieve the complete message.
        """
        kwargs: dict = {
            "model": self._model,
            "max_tokens": request.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS,
            "messages": [{"role": "user", "content": content}],
        }
        if request.system_prompt:
            kwargs["system"] = request.system_prompt
        if request.response_schema:
            kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": transform_schema(resolve_schema(request.response_schema))}
            }

        logger.info("调用 %s 文本 SDK kwargs=%s", self.name, format_kwargs_for_log(kwargs))
        try:
            async with self._client.messages.stream(**kwargs) as stream:
                message = await stream.get_final_message()
        except BadRequestError as exc:
            if request.response_schema:
                logger.warning("原生 output_config 失败 (%s)，降级到 prompt 注入路径", exc)
                return None
            raise

        usage = message.usage
        # Anthropic input_tokens excludes cache reads/writes: add both for total input. Report cache writes by two TTL tiers;
        # without tier details, use the default 5-minute tier.
        cache_read = usage.cache_read_input_tokens or 0
        cache_write = usage.cache_creation_input_tokens or 0
        breakdown = usage.cache_creation
        cache_write_1h = breakdown.ephemeral_1h_input_tokens if breakdown is not None else 0
        cache_write_5m = breakdown.ephemeral_5m_input_tokens if breakdown is not None else cache_write
        input_tokens = usage.input_tokens + cache_read + cache_write
        text = "".join(block.text for block in message.content if block.type == "text").strip()
        truncated = check_truncation(
            message.stop_reason,
            provider=PROVIDER_ANTHROPIC,
            model=self._model,
            output_tokens=usage.output_tokens,
            structured=structured,
        )
        return TextGenerationResult(
            text=text,
            provider=PROVIDER_ANTHROPIC,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=usage.output_tokens,
            truncated=truncated,
            cache_read_tokens=cache_read,
            cache_write_5m_tokens=cache_write_5m,
            cache_write_1h_tokens=cache_write_1h,
        )


def _build_user_content(request: TextGenerationRequest) -> list[dict]:
    """Compose user message content with image blocks before text."""
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
