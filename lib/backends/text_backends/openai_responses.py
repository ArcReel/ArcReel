"""OpenAIResponsesTextBackend — 经 OpenAI Responses API（``POST /v1/responses``）生成文本。

``o3-pro``、``gpt-5.5-pro``、``gpt-5.3-codex`` 等模型只开放 Responses，Chat Completions 调不了。
结构化输出编码为 ``text.format``（json_schema）；供应商拒收或未强制 schema 时降级到 prompt
注入路径，降级仍走 Responses，只支持 Responses 的模型也能完成。
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
    merge_billed_tokens,
    prompt_json_fallback,
    resolve_schema,
    structured_fallback_reason,
    truncate_for_log,
)
from lib.backends.text_backends.openai import DEFAULT_MODEL
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

# Responses 以 status=incomplete + incomplete_details.reason 表达截断，不用 finish_reason。
_TRUNCATION_REASONS = ("max_output_tokens",)


class OpenAIResponsesTextBackend:
    """OpenAI Responses 文本后端：原生 ``text.format`` 优先，不合规时降级到 prompt 注入。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        provider_name: str = PROVIDER_OPENAI,
    ):
        # 禁用 SDK 内置重试，由本层 _generate_once() 统一管理重试策略
        self._client = create_openai_client(api_key=api_key, base_url=base_url, max_retries=0)
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
        """生成文本回复。

        本方法不带重试装饰器：瞬态错误重试在 :meth:`_generate_once` 层完成，降级尝试的失败
        不会连带重放已成功的原生调用。
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
            # 原生请求不声明 strict（见 _generate_once），复验同样用 strict=False，容忍可强转值。
            fallback_reason = structured_fallback_reason(native.text, request.response_schema, strict=False)
            if fallback_reason:
                logger.warning(
                    "原生 text.format %s，降级到 prompt 注入路径；模型原始输出：%s",
                    fallback_reason,
                    truncate_for_log(native.text),
                )
                result = await self._prompt_json_fallback(request)
                # 这次原生 200 调用已被计费，把它的 token 并入降级结果。
                result.input_tokens = merge_billed_tokens(result.input_tokens, native.input_tokens)
                result.output_tokens = merge_billed_tokens(result.output_tokens, native.output_tokens)
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
        """单次 Responses 调用：构造请求、解析与截断处理，瞬态错误重试。

        ``structured`` 按本次 generate() 的原始诉求传入：prompt 注入降级把 ``response_schema``
        置空后复用本方法，那仍是一次结构化输出尝试，截断要硬错误而非仅告警。
        """
        # 带图请求要读本地图片编码 data URI，放线程里构建，避免阻塞事件循环
        input_items = await asyncio.to_thread(_build_input, request)
        # store=False：与 Chat Completions 的默认一致，不在 OpenAI 侧留存对话。
        kwargs: dict = {"model": self._model, "input": input_items, "store": False}
        if request.system_prompt:
            kwargs["instructions"] = request.system_prompt
        if request.max_output_tokens is not None:
            kwargs["max_output_tokens"] = request.max_output_tokens
        if request.response_schema:
            # 不声明 strict：strict 要求每个属性都进 required、对象都禁 additionalProperties，
            # 业务 schema 的可选字段与默认值普遍不满足，声明了只会整次被拒收。是否合规由复验判定。
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
            # 部分网关以 200 + status=failed 回报失败，output_text 为空，不能当成功结果
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
            # Responses 的 input_tokens 含缓存命中，按现有字段上报总输入。
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=output_tokens,
            truncated=truncated,
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
