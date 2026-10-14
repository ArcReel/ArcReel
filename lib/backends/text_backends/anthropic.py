"""AnthropicTextBackend — Anthropic Messages 协议的文本生成后端。

经国内厂商与中转站的 Anthropic 协议入口调用。结构化输出走 Messages 原生的
``output_config.format``，不经 OpenAI 兼容层（后者忽略 ``response_format``）；图片走图片内容块。
"""

from __future__ import annotations

import asyncio
import logging

from anthropic import (
    APIConnectionError,
    APITimeoutError,
    AsyncAnthropic,
    BadRequestError,
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
    merge_billed_tokens,
    prompt_json_fallback,
    resolve_schema,
    structured_fallback_reason,
    truncate_for_log,
)
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
    """Anthropic Messages 文本生成后端，只服务自定义供应商的 ``anthropic-messages`` 端点。

    凭证只取自定义供应商自己的 api_key 与 base_url，与 Agent 的 Anthropic 凭证隔离（ADR 0017）。
    """

    def __init__(self, *, api_key: str, model: str, base_url: str | None = None):
        # base_url 为空时显式回填官方地址，断掉 SDK 对 ANTHROPIC_BASE_URL 的环境回落；
        # 禁用 SDK 内置重试，由 _generate_native 统一管理。
        self._client = AsyncAnthropic(
            api_key=api_key,
            base_url=(base_url or "").strip() or OFFICIAL_ANTHROPIC_BASE_URL,
            max_retries=0,
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
        """生成文本回复。

        本方法不带重试装饰器：瞬态错误重试在 :meth:`_generate_native` 层完成，复验与降级
        不进重试范围，免得降级失败连带重放已成功的原生调用。
        """
        # 带图请求要读本地图片做 base64，放线程里构建，避免阻塞事件循环
        content = await asyncio.to_thread(_build_user_content, request)
        structured = bool(request.response_schema)
        native = await self._generate_native(request, content, structured=structured)

        if native is None:
            # 网关不接受 output_config（schema 错误），结构化输出整体降级
            return await self._prompt_json_fallback(request)

        if request.response_schema:
            # 中转站可能静默忽略 output_config 返回散文或违例 JSON。transform_schema 会把
            # 不支持的约束挪进 description，原生请求等同未声明 strict，复验用 strict=False。
            fallback_reason = structured_fallback_reason(native.text, request.response_schema, strict=False)
            if fallback_reason:
                logger.warning(
                    "原生 output_config %s，降级到 prompt 注入路径；模型原始输出：%s",
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
        async def _once(fb_request: TextGenerationRequest) -> TextGenerationResult:
            content = await asyncio.to_thread(_build_user_content, fb_request)
            # 降级请求已不带 output_config，不会再返回 schema 错误的降级信号
            result = await self._generate_native(fb_request, content, structured=True)
            assert result is not None
            return result

        return await prompt_json_fallback(request, _once, provider=PROVIDER_ANTHROPIC, model=self._model)

    @with_retry_async(max_attempts=4, backoff_seconds=(2, 4, 8), retryable_errors=ANTHROPIC_RETRYABLE_ERRORS)
    async def _generate_native(
        self, request: TextGenerationRequest, content: list[dict], *, structured: bool
    ) -> TextGenerationResult | None:
        """单次 Messages 调用：瞬态错误重试；output_config 被拒时返回 ``None`` 作为降级信号。

        输出上限可达数万 token，非流式请求会被 SDK 以超时风险拒绝，固定走流式再取完整消息。
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
        # 现有字段只有总输入：Anthropic 的 input_tokens 不含缓存，缓存读写须加回。
        input_tokens = (
            usage.input_tokens + (usage.cache_read_input_tokens or 0) + (usage.cache_creation_input_tokens or 0)
        )
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
