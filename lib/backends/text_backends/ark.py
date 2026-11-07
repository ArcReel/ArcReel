"""ArkTextBackend: Volcengine Ark text-generation backend."""

from __future__ import annotations

import asyncio
import logging

from openai import OpenAI

from lib.backends.ark_shared import ark_base_url, create_ark_client, resolve_ark_api_key
from lib.backends.providers import PROVIDER_ARK
from lib.backends.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    merge_billed_usage,
)
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "doubao-seed-2-1-lite-260915"


class ArkTextBackend:
    """Ark (Volcengine Ark) text-generation backend."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        # Instructor requires an openai.OpenAI instance, incompatible with Ark SDK client types.
        # Ark APIs are OpenAI-compatible, so create an additional native OpenAI client for fallback.
        resolved_key = resolve_ark_api_key(api_key)
        effective_base_url = ark_base_url(base_url)
        self._client = create_ark_client(api_key=resolved_key, base_url=effective_base_url)
        self._openai_client = OpenAI(base_url=effective_base_url, api_key=resolved_key)
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[TextCapability] = self._resolve_capabilities()

    def _resolve_capabilities(self) -> set[TextCapability]:
        """Build capabilities from model declarations in the model catalog."""
        from lib.catalog import query as catalog

        base = {TextCapability.TEXT_GENERATION, TextCapability.VISION}
        # One backend class serves ark and ark-agent-plan with different model ID naming formats;
        # a match in either provider can resolve STRUCTURED_OUTPUT.
        for provider_id in ("ark", "ark-agent-plan"):
            model_info = catalog.get_model(provider_id, self._model)
            if model_info and TextCapability.STRUCTURED_OUTPUT in model_info.capabilities:
                base.add(TextCapability.STRUCTURED_OUTPUT)
                break
        # Unregistered models do not gain STRUCTURED_OUTPUT: prefer Instructor fallback over failing native APIs.
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
        """Generate a text response.

        No retry decorator here: transient errors retry per call (:meth:`_call_chat_completions`
        and :meth:`_structured_fallback`). Including validation/fallback in the retry scope would let failed
        fallback attempts replay successful native calls, multiplying retries.
        """
        if request.response_schema:
            return await self._generate_structured(request)
        return await self._generate_plain(request)

    @with_retry_async()
    async def _call_chat_completions(self, kwargs: dict, *, structured: bool) -> TextGenerationResult:
        """One chat.completions call: logging, request, parsing, truncation handling, and transient-error retries."""
        logger.info("调用 %s 文本 SDK kwargs=%s", self.name, format_kwargs_for_log(kwargs))
        response = await asyncio.to_thread(
            self._client.chat.completions.create,
            **kwargs,
        )
        return self._parse_chat_response(response, structured=structured)

    async def _generate_plain(self, request: TextGenerationRequest) -> TextGenerationResult:
        messages = self._build_messages(request)
        kwargs: dict = {"model": self._model, "messages": messages}
        if request.max_output_tokens is not None:
            kwargs["max_tokens"] = request.max_output_tokens
        return await self._call_chat_completions(kwargs, structured=False)

    async def _generate_structured(self, request: TextGenerationRequest) -> TextGenerationResult:
        messages = self._build_messages(request)

        if TextCapability.STRUCTURED_OUTPUT in self._capabilities:
            from lib.backends.text_backends.base import resolve_schema, structured_fallback_reason

            if request.response_schema is None:
                raise ValueError("structured 模式要求 response_schema 非空")
            schema = resolve_schema(request.response_schema)
            kwargs: dict = {
                "model": self._model,
                "messages": messages,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": schema},
                },
            }
            if request.max_output_tokens is not None:
                kwargs["max_tokens"] = request.max_output_tokens
            from lib.backends.text_backends.base import TextOutputTruncatedError

            try:
                native = await self._call_chat_completions(kwargs, structured=True)
            except TextOutputTruncatedError:
                # Truncation is an actionable hard error, not native-channel incompatibility. Resending the same
                # inevitably truncated request through Instructor is pointless: propagate it so callers switch models/reduce scope.
                raise
            except Exception as exc:
                logger.warning("原生 response_format 调用或解析失败 (%s)，降级到 Instructor/json_object 路径", exc)
                return await self._structured_fallback(request, messages)

            # Revalidate successful HTTP 200 responses against schemas; fall back for non-JSON or schema violations.
            # Models/proxies without strict enforcement may return invalid JSON that otherwise leaks into downstream validation.
            # Ark native response_format omits strict; revalidate with strict=False too, avoiding false violations for coercible values.
            fallback_reason = structured_fallback_reason(native.text, request.response_schema, strict=False)
            if fallback_reason:
                from lib.backends.text_backends.base import truncate_for_log

                logger.warning(
                    "原生 response_format %s，降级到带校验的 Instructor 路径；模型原始输出：%s",
                    fallback_reason,
                    truncate_for_log(native.text),
                )
                result = await self._structured_fallback(request, messages)
                # This native 200 call was already billed by the proxy; merge its tokens into the fallback result.
                merge_billed_usage(result, native)
                return result
            return native

        return await self._structured_fallback(request, messages)

    @with_retry_async()
    async def _structured_fallback(self, request: TextGenerationRequest, messages: list[dict]) -> TextGenerationResult:
        """Instructor / json_object fallback path.

        instructor_fallback_sync has no transient retries itself, so add retries here, covering only fallback;
        failures do not replay successful native calls.
        """
        from lib.backends.text_backends.instructor_support import instructor_fallback_sync

        return await asyncio.to_thread(
            instructor_fallback_sync,
            client=self._openai_client,
            model=self._model,
            messages=messages,
            response_schema=request.response_schema,
            provider=PROVIDER_ARK,
            max_tokens=request.max_output_tokens,
        )

    def _build_messages(self, request: TextGenerationRequest) -> list[dict]:
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

    def _parse_chat_response(self, response, *, structured: bool) -> TextGenerationResult:
        from lib.backends.text_backends.base import chat_completion_result, check_truncation

        choice = response.choices[0]
        text = choice.message.content
        usage = getattr(response, "usage", None)
        truncated = check_truncation(
            getattr(choice, "finish_reason", None),
            provider=PROVIDER_ARK,
            model=self._model,
            output_tokens=getattr(usage, "completion_tokens", None),
            structured=structured,
        )
        return chat_completion_result(
            text=text.strip() if isinstance(text, str) else str(text),
            provider=PROVIDER_ARK,
            model=self._model,
            usage=usage,
            truncated=truncated,
        )
