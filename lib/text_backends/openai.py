"""OpenAITextBackend — OpenAI 文本生成后端。"""

from __future__ import annotations

import logging

import instructor
from openai import AsyncOpenAI, BadRequestError

from lib.openai_shared import OPENAI_RETRYABLE_ERRORS, create_openai_client
from lib.providers import PROVIDER_OPENAI
from lib.retry import with_retry_async
from lib.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    resolve_schema,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5.4-mini"


class OpenAITextBackend:
    """OpenAI 文本生成后端，支持 Chat Completions API。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        # 禁用 SDK 内置重试，由本层 generate() 统一管理重试策略
        self._client = create_openai_client(api_key=api_key, base_url=base_url, max_retries=0)
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[TextCapability] = {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }

    @property
    def name(self) -> str:
        return PROVIDER_OPENAI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[TextCapability]:
        return self._capabilities

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        """生成文本回复。

        瞬态错误（429/500/503/网络）由 _call_api 的 @with_retry_async 自动重试。
        当 response_schema 已设置且调用抛出 schema 不兼容错误时，
        自动降级到 Instructor 结构化输出（不做无效重试）。
        """
        messages = _build_messages(request)
        kwargs: dict = {"model": self._model, "messages": messages}

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

        try:
            return await self._call_api(**kwargs)
        except Exception as exc:
            # schema 不兼容（可能被代理包装成任意状态码）→ 降级到 Instructor
            if request.response_schema and _is_schema_error(exc):
                logger.warning(
                    "原生 response_format 失败 (%s)，降级到 Instructor 路径",
                    exc,
                )
                return await _instructor_fallback(self._client, self._model, request, messages)
            raise

    @with_retry_async(max_attempts=4, backoff_seconds=(2, 4, 8), retryable_errors=OPENAI_RETRYABLE_ERRORS)
    async def _call_api(self, **kwargs) -> TextGenerationResult:
        """调用 OpenAI API，由装饰器处理瞬态错误重试。"""
        response = await self._client.chat.completions.create(**kwargs)
        usage = response.usage
        return TextGenerationResult(
            text=response.choices[0].message.content or "",
            provider=PROVIDER_OPENAI,
            model=self._model,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )


def _build_messages(request: TextGenerationRequest) -> list[dict]:
    """将 TextGenerationRequest 转为 OpenAI messages 格式。"""
    messages: list[dict] = []

    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})

    # 构建 user message
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


_SCHEMA_ERROR_KEYWORDS = (
    "response_schema",
    "json_schema",
    "Unknown name",
    "Cannot find field",
    "Invalid JSON payload",
)


def _is_schema_error(exc: BaseException) -> bool:
    """判断异常是否为 JSON Schema 不兼容导致的错误。

    除了标准的 400 BadRequestError，一些 OpenAI 兼容代理（如 Gemini
    兼容端点）会将上游 schema 错误包装成其他状态码（如 429），
    因此也检查错误信息中是否包含 schema 相关关键字。
    """
    if isinstance(exc, BadRequestError):
        return True
    # 代理可能把上游 schema 错误包装成非 400 状态码
    error_str = str(exc)
    return any(kw in error_str for kw in _SCHEMA_ERROR_KEYWORDS)


async def _instructor_fallback(
    client: AsyncOpenAI,
    model: str,
    request: TextGenerationRequest,
    messages: list[dict],
) -> TextGenerationResult:
    """Instructor 降级：当原生 response_format 不可用时的备选路径。

    - response_schema 为 Pydantic 类：使用 instructor 的 create_with_completion
    - response_schema 为 dict：回退到无结构化输出的普通调用
    """
    if isinstance(request.response_schema, type):
        # Pydantic 模型 — 用 Instructor 做 prompt 注入 + 解析 + 重试
        patched = instructor.from_openai(client, mode=instructor.Mode.MD_JSON)
        result, completion = await patched.chat.completions.create_with_completion(
            model=model,
            messages=messages,
            response_model=request.response_schema,
            max_retries=2,
        )
        json_text = result.model_dump_json()
        input_tokens = None
        output_tokens = None
        if completion.usage:
            input_tokens = completion.usage.prompt_tokens
            output_tokens = completion.usage.completion_tokens
        return TextGenerationResult(
            text=json_text,
            provider=PROVIDER_OPENAI,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    else:
        # dict schema — 无法用 Instructor（需要 Pydantic 类），
        # 回退到 json_object 模式（比原生 json_schema 兼容性更广）
        logger.info("response_schema 为 dict，无法使用 Instructor，回退到 json_object 模式")
        # OpenAI API 要求 prompt 中包含 "JSON" 关键字才能启用 json_object 模式
        fb_messages = list(messages)
        if not any("JSON" in (m.get("content") or "") for m in fb_messages):
            sys_idx = next((i for i, m in enumerate(fb_messages) if m.get("role") == "system"), None)
            if sys_idx is not None:
                orig = fb_messages[sys_idx]
                fb_messages[sys_idx] = {**orig, "content": (orig.get("content") or "") + "\nRespond in JSON format."}
            else:
                fb_messages.insert(0, {"role": "system", "content": "Respond in JSON format."})
        response = await client.chat.completions.create(
            model=model,
            messages=fb_messages,
            response_format={"type": "json_object"},
        )
        usage = response.usage
        return TextGenerationResult(
            text=response.choices[0].message.content or "",
            provider=PROVIDER_OPENAI,
            model=model,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )
