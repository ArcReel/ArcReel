"""ArkTextBackend — 火山方舟文本生成后端。"""

from __future__ import annotations

import asyncio
import logging

from openai import OpenAI

from lib.ark_shared import ARK_BASE_URL, create_ark_client, resolve_ark_api_key
from lib.logging_utils import format_kwargs_for_log
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
    """Ark (火山方舟) 文本生成后端。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        # Instructor 要求 openai.OpenAI 实例；Ark SDK client 类型不兼容，
        # 但 Ark API 是 OpenAI 兼容的，因此额外创建原生 OpenAI 客户端供降级使用。
        resolved_key = resolve_ark_api_key(api_key)
        effective_base_url = base_url or ARK_BASE_URL
        self._client = create_ark_client(api_key=resolved_key, base_url=effective_base_url)
        self._openai_client = OpenAI(base_url=effective_base_url, api_key=resolved_key)
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[TextCapability] = self._resolve_capabilities()

    def _resolve_capabilities(self) -> set[TextCapability]:
        """根据 PROVIDER_REGISTRY 中的模型声明构建能力集合。"""
        from lib.config.registry import PROVIDER_REGISTRY

        base = {TextCapability.TEXT_GENERATION, TextCapability.VISION}
        # 同一 backend 类同时服务 ark 与 ark-agent-plan，模型 ID 命名格式不同，
        # 任一 provider 命中即可解析 STRUCTURED_OUTPUT。
        for provider_id in ("ark", "ark-agent-plan"):
            provider_meta = PROVIDER_REGISTRY.get(provider_id)
            if provider_meta is None:
                continue
            model_info = provider_meta.models.get(self._model)
            if model_info and TextCapability.STRUCTURED_OUTPUT in model_info.capabilities:
                base.add(TextCapability.STRUCTURED_OUTPUT)
                break
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
        """生成文本回复。

        本方法不带重试装饰器：瞬态错误重试在单次调用层（:meth:`_call_chat_completions`
        与 :meth:`_structured_fallback`）完成。若把复验/降级也包进重试范围，降级尝试的
        失败会连带重放已成功的原生调用（重试叠乘）。
        """
        if request.response_schema:
            return await self._generate_structured(request)
        return await self._generate_plain(request)

    @with_retry_async()
    async def _call_chat_completions(self, kwargs: dict) -> TextGenerationResult:
        """单次 chat.completions 调用：日志、发请求、解析与截断告警，瞬态错误重试。"""
        logger.info("调用 %s 文本 SDK kwargs=%s", self.name, format_kwargs_for_log(kwargs))
        response = await asyncio.to_thread(
            self._client.chat.completions.create,
            **kwargs,
        )
        return self._parse_chat_response(response)

    async def _generate_plain(self, request: TextGenerationRequest) -> TextGenerationResult:
        messages = self._build_messages(request)
        kwargs: dict = {"model": self._model, "messages": messages}
        if request.max_output_tokens is not None:
            kwargs["max_tokens"] = request.max_output_tokens
        return await self._call_chat_completions(kwargs)

    async def _generate_structured(self, request: TextGenerationRequest) -> TextGenerationResult:
        messages = self._build_messages(request)

        if TextCapability.STRUCTURED_OUTPUT in self._capabilities:
            from lib.text_backends.base import resolve_schema, structured_fallback_reason

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
            try:
                native = await self._call_chat_completions(kwargs)
            except Exception as exc:
                logger.warning("原生 response_format 调用或解析失败 (%s)，降级到 Instructor/json_object 路径", exc)
                return await self._structured_fallback(request, messages)

            # 成功路径复验：HTTP 200 后校验返回是否真满足 schema，违例（非 JSON / 违反 schema）则降级。
            # 不严格强制 schema 的模型/中转会返回违例 JSON，若直接放行会一路漏到下游校验才报错。
            # ark 原生 response_format 未声明 strict，复验同样用 strict=False，避免对可强转值误判违例。
            fallback_reason = structured_fallback_reason(native.text, request.response_schema, strict=False)
            if fallback_reason:
                logger.warning("原生 response_format %s，降级到带校验的 Instructor 路径", fallback_reason)
                result = await self._structured_fallback(request, messages)
                # 这次原生 200 调用已被代理计费，把它的 token 并入降级结果，否则会系统性漏记。
                # 仅在至少一侧有计量时相加；两侧皆 None（未追踪）保持 None，不塌成字面 0 token。
                if result.input_tokens is not None or native.input_tokens is not None:
                    result.input_tokens = (result.input_tokens if result.input_tokens is not None else 0) + (
                        native.input_tokens if native.input_tokens is not None else 0
                    )
                if result.output_tokens is not None or native.output_tokens is not None:
                    result.output_tokens = (result.output_tokens if result.output_tokens is not None else 0) + (
                        native.output_tokens if native.output_tokens is not None else 0
                    )
                return result
            return native

        return await self._structured_fallback(request, messages)

    @with_retry_async()
    async def _structured_fallback(self, request: TextGenerationRequest, messages: list[dict]) -> TextGenerationResult:
        """Instructor / json_object 降级路径。

        instructor_fallback_sync 自身不做瞬态重试，这里补一层重试；范围仅覆盖降级自身，
        失败不会重放已成功的原生调用。
        """
        from lib.text_backends.instructor_support import instructor_fallback_sync

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
            from lib.image_backends.base import image_to_base64_data_uri

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

    def _parse_chat_response(self, response) -> TextGenerationResult:
        from lib.text_backends.base import warn_if_truncated

        choice = response.choices[0]
        text = choice.message.content
        input_tokens = getattr(getattr(response, "usage", None), "prompt_tokens", None)
        output_tokens = getattr(getattr(response, "usage", None), "completion_tokens", None)
        warn_if_truncated(
            getattr(choice, "finish_reason", None),
            provider=PROVIDER_ARK,
            model=self._model,
            output_tokens=output_tokens,
        )
        return TextGenerationResult(
            text=text.strip() if isinstance(text, str) else str(text),
            provider=PROVIDER_ARK,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
