"""OpenAITextBackend: OpenAI text-generation backend."""

from __future__ import annotations

import asyncio
import logging

from openai import AsyncOpenAI, BadRequestError

from lib.backends.openai_shared import OPENAI_RETRYABLE_ERRORS, create_openai_client
from lib.backends.providers import PROVIDER_DASHSCOPE, PROVIDER_OPENAI
from lib.backends.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    TokenParam,
    chat_completion_result,
    check_truncation,
    merge_billed_usage,
    resolve_schema,
    strip_leading_think_block,
    structured_fallback_reason,
    truncate_for_log,
)
from lib.backends.transport_layer import TransportLayer
from lib.config.url_utils import is_official_openai_base_url
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5.4-mini"


class OpenAITextBackend:
    """OpenAI text backend supporting Chat Completions API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
        provider_name: str = PROVIDER_OPENAI,
    ):
        # Disable SDK retries; this layer's generate() centrally manages retry policy.
        self._client = create_openai_client(
            api_key=api_key, base_url=base_url, max_retries=0, transport_layer=transport_layer
        )
        self._model = model or DEFAULT_MODEL
        # Providers reusing OpenAI-compatible protocols, such as dashscope, must bill under the actual provider,
        # otherwise price lookup selects OpenAI USD rates instead of their own pricing.
        self._provider_name = provider_name
        # Official endpoints deprecated max_tokens (reasoning models reject it); use max_completion_tokens.
        # Third-party compatible endpoints (custom providers, dashscope, etc.) may not support it; conservatively retain max_tokens.
        self._max_tokens_param: TokenParam = (
            "max_completion_tokens" if is_official_openai_base_url(base_url) else "max_tokens"
        )
        self._capabilities: set[TextCapability] = self._resolve_capabilities()

    def _resolve_capabilities(self) -> set[TextCapability]:
        """Official OpenAI/custom providers keep fixed capabilities; DashScope follows registry declarations."""
        base = {TextCapability.TEXT_GENERATION, TextCapability.VISION}
        if self._provider_name != PROVIDER_DASHSCOPE:
            base.add(TextCapability.STRUCTURED_OUTPUT)
            return base

        from lib.catalog import query as catalog

        model_info = catalog.get_model(self._provider_name, self._model)
        if model_info and TextCapability.STRUCTURED_OUTPUT in model_info.capabilities:
            base.add(TextCapability.STRUCTURED_OUTPUT)
        return base

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

        No retry decorator here: transient retries occur in :meth:`_generate_native` and
        :func:`_instructor_fallback`. Including validation/fallback in retry scope would let failed
        fallback attempts replay successful native calls (multiplying retries). Fallback exception messages also
        contain dynamic model text that could accidentally match retry string patterns.
        """
        # Build image requests in threads: local image reads/data URI encoding must not block the event loop.
        messages = await asyncio.to_thread(_build_messages, request)
        if request.response_schema and TextCapability.STRUCTURED_OUTPUT not in self._capabilities:
            logger.info("%s/%s 未声明原生结构化输出，直接走 Instructor", self._provider_name, self._model)
            return await _instructor_fallback(
                self._client,
                self._model,
                request,
                messages,
                provider=self._provider_name,
                token_param=self._max_tokens_param,
            )

        native = await self._generate_native(request, messages)
        if native is None:
            # Native response_format incompatibility (schema errors): fall back for the entire structured-output attempt.
            return await _instructor_fallback(
                self._client,
                self._model,
                request,
                messages,
                provider=self._provider_name,
                token_param=self._max_tokens_param,
            )

        if request.response_schema:
            fallback_reason = structured_fallback_reason(native.text, request.response_schema)
            if fallback_reason:
                logger.warning(
                    "原生 response_format %s，降级到带校验的 Instructor 路径；模型原始输出：%s",
                    fallback_reason,
                    truncate_for_log(native.text),
                )
                result = await _instructor_fallback(
                    self._client,
                    self._model,
                    request,
                    messages,
                    provider=self._provider_name,
                    token_param=self._max_tokens_param,
                )
                # This native 200 call was billed by the proxy; merge its tokens into fallback results.
                merge_billed_usage(result, native)
                return result

        return native

    @with_retry_async(max_attempts=4, backoff_seconds=(2, 4, 8), retryable_errors=OPENAI_RETRYABLE_ERRORS)
    async def _generate_native(
        self, request: TextGenerationRequest, messages: list[dict]
    ) -> TextGenerationResult | None:
        """One native call: construct kwargs, send requests, parse, warn on truncation, and retry transient errors.

        Return ``None`` (fallback signal) for incompatible schemas instead of raising: proxies may wrap upstream schema errors
        as statuses such as 429. Exceptions passing through retry decorators would incorrectly match transient-error
        string patterns and waste retries.
        """
        kwargs: dict = {"model": self._model, "messages": messages}
        if request.max_output_tokens is not None:
            kwargs[self._max_tokens_param] = request.max_output_tokens

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

        logger.info("调用 %s 文本 SDK kwargs=%s", self.name, format_kwargs_for_log(kwargs))
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if request.response_schema and _is_schema_error(exc):
                logger.warning(
                    "原生 response_format 失败 (%s)，降级到 Instructor 路径",
                    exc,
                )
                return None
            raise

        usage = response.usage
        choice = response.choices[0]
        output_tokens = usage.completion_tokens if usage else None
        raw_text = choice.message.content or ""
        # Thinking models prepend thinking blocks inside content, followed by the body (JSON for structured outputs).
        # Revalidation and returned results use only the body, excluding thinking blocks.
        text = strip_leading_think_block(raw_text)
        if text != raw_text:
            logger.info(
                "%s/%s 输出开头带思考块，已剥离（%d 字符）",
                self._provider_name,
                self._model,
                len(raw_text) - len(text),
            )

        truncated = check_truncation(
            getattr(choice, "finish_reason", None),
            provider=self._provider_name,
            model=self._model,
            output_tokens=output_tokens,
            structured=bool(request.response_schema),
        )
        return chat_completion_result(
            text=text, provider=self._provider_name, model=self._model, usage=usage, truncated=truncated
        )


def _build_messages(request: TextGenerationRequest) -> list[dict]:
    """Convert TextGenerationRequest into OpenAI messages."""
    messages: list[dict] = []

    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})

    # Build user messages.
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


_SCHEMA_ERROR_KEYWORDS = (
    "response_schema",
    "json_schema",
    "Unknown name",
    "Cannot find field",
    "Invalid JSON payload",
)


def _is_schema_error(exc: BaseException) -> bool:
    """Determine whether an exception comes from JSON Schema incompatibility.

    Besides standard 400 BadRequestError, some OpenAI-compatible proxies (such as Gemini
    compatible endpoints) wrap upstream schema errors in other statuses (such as 429),
    so also inspect error messages for schema-related keywords.
    """
    if isinstance(exc, BadRequestError):
        return True
    # Proxies may wrap upstream schema errors as statuses other than 400.
    error_str = str(exc)
    return any(kw in error_str for kw in _SCHEMA_ERROR_KEYWORDS)


@with_retry_async(max_attempts=4, backoff_seconds=(2, 4, 8), retryable_errors=OPENAI_RETRYABLE_ERRORS)
async def _instructor_fallback(
    client: AsyncOpenAI,
    model: str,
    request: TextGenerationRequest,
    messages: list[dict],
    *,
    provider: str = PROVIDER_OPENAI,
    token_param: TokenParam = "max_tokens",
) -> TextGenerationResult:
    """Instructor fallback when native response_format is unavailable.

    instructor_fallback_async has no transient retries itself; add retries with native-call settings here,
    scoped only to fallback so failures cannot replay successful native calls.
    """
    from lib.backends.text_backends.instructor_support import instructor_fallback_async

    return await instructor_fallback_async(
        client=client,
        model=model,
        messages=messages,
        response_schema=request.response_schema,
        provider=provider,
        max_tokens=request.max_output_tokens,
        token_param=token_param,
    )
