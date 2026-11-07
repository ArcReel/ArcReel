"""OpenAIResponsesTextBackend: text generation through OpenAI Responses API (``POST /v1/responses``).

Models such as ``o3-pro``, ``gpt-5.5-pro``, and ``gpt-5.3-codex`` expose Responses only, unavailable through Chat Completions.
Encode structured output as ``text.format`` (json_schema); fall back to prompt injection
when providers reject or do not enforce schemas. Fallback still uses Responses, supporting Responses-only models.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from openai import BadRequestError

from lib.backends.openai_shared import OPENAI_RETRYABLE_ERRORS, create_openai_client
from lib.backends.providers import PROVIDER_OPENAI
from lib.backends.text_backends.base import (
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
from lib.backends.text_backends.openai import DEFAULT_MODEL
from lib.backends.transport_layer import TransportLayer
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

# Responses reports truncation through status=incomplete + incomplete_details.reason, not finish_reason.
_TRUNCATION_REASONS = ("max_output_tokens",)


class OpenAIResponsesTextBackend:
    """OpenAI Responses text backend: prefer native ``text.format``, falling back to prompt injection for noncompliance."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
        provider_name: str = PROVIDER_OPENAI,
    ):
        # Disable built-in SDK retries; _generate_once() centrally controls retry policy.
        self._client = create_openai_client(
            api_key=api_key, base_url=base_url, max_retries=0, transport_layer=transport_layer
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
        """Generate a text response.

        No retry decorator here: transient errors retry within :meth:`_generate_once`, so failed
        fallback attempts do not replay already-successful native calls.
        """
        structured = request.response_schema is not None
        try:
            native = await self._generate_once(request, structured=structured)
        except BadRequestError as exc:
            if not structured:
                raise
            logger.warning("原生 text.format 被拒收 (%s)，降级到 prompt 注入路径", exc)
            return await self._prompt_json_fallback(request)

        if structured:
            # Native requests omit strict (see _generate_once); revalidation also uses strict=False to tolerate coercible values.
            fallback_reason = structured_fallback_reason(native.text, request.response_schema, strict=False)
            if fallback_reason:
                logger.warning(
                    "原生 text.format %s，降级到 prompt 注入路径；模型原始输出：%s",
                    fallback_reason,
                    truncate_for_log(native.text),
                )
                result = await self._prompt_json_fallback(request)
                # This native 200 call was billed; merge its tokens into the fallback result.
                merge_billed_usage(result, native)
                return result

        return native

    async def _prompt_json_fallback(self, request: TextGenerationRequest) -> TextGenerationResult:
        return await prompt_json_fallback(
            request,
            partial(self._generate_once, structured=True),
            provider=self._provider_name,
            model=self._model,
        )

    @with_retry_async(max_attempts=4, backoff_seconds=(2, 4, 8), retryable_errors=OPENAI_RETRYABLE_ERRORS)
    async def _generate_once(self, request: TextGenerationRequest, *, structured: bool) -> TextGenerationResult:
        """One Responses call: construct requests, parse and handle truncation, retry transient errors.

        ``structured`` carries the original generate() intent: prompt-injection fallback reuses this
        method after clearing ``response_schema``, but remains a structured-output attempt, so truncation
        must be a hard error rather than a warning.
        """
        # Build image requests in a thread: reading local images and encoding data URIs must not block the event loop.
        input_items = await asyncio.to_thread(_build_input, request)
        # store=False: match Chat Completions defaults without retaining conversations on OpenAI servers.
        kwargs: dict = {"model": self._model, "input": input_items, "store": False}
        if request.system_prompt:
            kwargs["instructions"] = request.system_prompt
        if request.max_output_tokens is not None:
            kwargs["max_output_tokens"] = request.max_output_tokens
        if request.response_schema:
            # Omit strict: it requires every property in required and additionalProperties forbidden on objects.
            # Business schemas commonly contain optional/default fields, so strict would reject whole requests. Revalidation determines compliance.
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "response",
                    "schema": resolve_schema(request.response_schema),
                }
            }

        logger.info("调用 %s 文本 SDK kwargs=%s", self.name, format_kwargs_for_log(kwargs))
        response = await self._client.responses.create(**kwargs)
        if response.status == "failed":
            # Some gateways report failure as 200 + status=failed with empty output_text; do not treat this as success.
            detail = response.error.message if response.error else "unknown error"
            raise RuntimeError(f"Responses 调用失败 (model={self._model}): {detail}")

        usage = response.usage
        output_tokens = usage.output_tokens if usage else None
        incomplete = response.incomplete_details
        truncated = check_truncation(
            incomplete.reason if response.status == "incomplete" and incomplete else None,
            provider=self._provider_name,
            model=self._model,
            output_tokens=output_tokens,
            structured=structured,
            truncation_values=_TRUNCATION_REASONS,
        )
        return TextGenerationResult(
            text=response.output_text,
            provider=self._provider_name,
            model=self._model,
            # Responses input_tokens is total input; cache hits are in input_tokens_details.cached_tokens.
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=output_tokens,
            truncated=truncated,
            cache_read_tokens=usage.input_tokens_details.cached_tokens if usage else None,
        )


def _build_input(request: TextGenerationRequest) -> list[dict]:
    """Convert TextGenerationRequest into Responses input lists; system_prompt goes through instructions."""
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
