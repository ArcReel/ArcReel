"""Instructor 降级支持 — 为不支持原生结构化输出的模型提供 prompt 注入 + 解析 + 重试。"""

from __future__ import annotations

import logging

import instructor
from instructor import Mode
from pydantic import BaseModel

from lib.text_backends.base import TextGenerationResult

logger = logging.getLogger(__name__)


def generate_structured_via_instructor(
    client,
    model: str,
    messages: list[dict],
    response_model: type[BaseModel],
    mode: Mode = Mode.MD_JSON,
    max_retries: int = 2,
    max_tokens: int | None = None,
) -> tuple[str, int | None, int | None]:
    """通过 Instructor 生成结构化输出（同步版，供 Ark 等同步 SDK 使用）。

    返回 (json_text, input_tokens, output_tokens)。
    """
    patched = instructor.from_openai(client, mode=mode)
    if patched is None:
        raise TypeError(
            f"instructor.from_openai() 返回 None — client 类型 {type(client).__name__} 不受支持，"
            "请传入 openai.OpenAI 或 openai.AsyncOpenAI 实例"
        )
    extra: dict = {"max_tokens": max_tokens} if max_tokens is not None else {}
    result, completion = patched.chat.completions.create_with_completion(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        response_model=response_model,
        max_retries=max_retries,
        **extra,
    )
    json_text = result.model_dump_json()

    input_tokens = None
    output_tokens = None
    if completion.usage:
        input_tokens = completion.usage.prompt_tokens
        output_tokens = completion.usage.completion_tokens

    return json_text, input_tokens, output_tokens


async def generate_structured_via_instructor_async(
    client,
    model: str,
    messages: list[dict],
    response_model: type[BaseModel],
    mode: Mode = Mode.MD_JSON,
    max_retries: int = 2,
    max_tokens: int | None = None,
) -> tuple[str, int | None, int | None]:
    """通过 Instructor 生成结构化输出（异步版，供 OpenAI AsyncOpenAI 使用）。

    返回 (json_text, input_tokens, output_tokens)。
    """
    patched = instructor.from_openai(client, mode=mode)
    if patched is None:
        raise TypeError(
            f"instructor.from_openai() 返回 None — client 类型 {type(client).__name__} 不受支持，"
            "请传入 openai.OpenAI 或 openai.AsyncOpenAI 实例"
        )
    extra: dict = {"max_tokens": max_tokens} if max_tokens is not None else {}
    result, completion = await patched.chat.completions.create_with_completion(  # type: ignore[misc]
        model=model,
        messages=messages,  # type: ignore[arg-type]
        response_model=response_model,
        max_retries=max_retries,
        **extra,
    )
    json_text = result.model_dump_json()

    input_tokens = None
    output_tokens = None
    if completion.usage:
        input_tokens = completion.usage.prompt_tokens
        output_tokens = completion.usage.completion_tokens

    return json_text, input_tokens, output_tokens


def inject_json_instruction(messages: list[dict]) -> list[dict]:
    """向 messages 注入 JSON 格式指令，确保 json_object 模式可用。

    OpenAI API 要求 prompt 中包含 "JSON" 关键字才能启用 json_object 模式。
    若 messages 中已包含 "JSON"，则原样返回副本。
    """
    fb_messages = list(messages)
    if any("JSON" in (m.get("content") or "") for m in fb_messages):
        return fb_messages
    sys_idx = next((i for i, m in enumerate(fb_messages) if m.get("role") == "system"), None)
    if sys_idx is not None:
        orig = fb_messages[sys_idx]
        fb_messages[sys_idx] = {**orig, "content": (orig.get("content") or "") + "\nRespond in JSON format."}
    else:
        fb_messages.insert(0, {"role": "system", "content": "Respond in JSON format."})
    return fb_messages


def instructor_fallback_sync(
    client,
    model: str,
    messages: list[dict],
    response_schema: dict | type | None,
    provider: str,
    max_tokens: int | None = None,
):
    """同步 Instructor 降级路径。

    - response_schema 为 Pydantic 类 → instructor create_with_completion
    - response_schema 为 dict → inject JSON instruction + json_object 模式

    供 Ark 等同步 SDK 后端使用（调用方用 asyncio.to_thread 包装）。
    不做重试，瞬态错误由调用方的重试循环统一处理。
    """
    if isinstance(response_schema, type):
        json_text, input_tokens, output_tokens = generate_structured_via_instructor(
            client=client,
            model=model,
            messages=messages,
            response_model=response_schema,
            max_tokens=max_tokens,
        )
        return TextGenerationResult(
            text=json_text,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    logger.info("response_schema 为 dict，无法使用 Instructor，回退到 json_object 模式")
    fb_messages = inject_json_instruction(messages)
    create_kwargs: dict = {
        "model": model,
        "messages": fb_messages,
        "response_format": {"type": "json_object"},
    }
    if max_tokens is not None:
        create_kwargs["max_tokens"] = max_tokens
    response = client.chat.completions.create(**create_kwargs)
    usage = getattr(response, "usage", None)
    choice = response.choices[0]
    text = choice.message.content or ""
    output_tokens = getattr(usage, "completion_tokens", None) if usage else None
    from lib.text_backends.base import warn_if_truncated

    warn_if_truncated(
        getattr(choice, "finish_reason", None),
        provider=provider,
        model=model,
        output_tokens=output_tokens,
    )
    return TextGenerationResult(
        text=text.strip() if isinstance(text, str) else str(text),
        provider=provider,
        model=model,
        input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
        output_tokens=output_tokens,
    )


async def instructor_fallback_async(
    client,
    model: str,
    messages: list[dict],
    response_schema: dict | type | None,
    provider: str,
    max_tokens: int | None = None,
):
    """异步 Instructor 降级路径。

    - response_schema 为 Pydantic 类 → instructor create_with_completion (async)
    - response_schema 为 dict → inject JSON instruction + json_object 模式 (async)

    供 OpenAI 等原生异步 SDK 后端使用。
    不做重试，瞬态错误由调用方的重试循环统一处理。
    """
    from lib.text_backends.base import TextGenerationResult

    if isinstance(response_schema, type):
        json_text, input_tokens, output_tokens = await generate_structured_via_instructor_async(
            client=client,
            model=model,
            messages=messages,
            response_model=response_schema,
            max_tokens=max_tokens,
        )
        return TextGenerationResult(
            text=json_text,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    logger.info("response_schema 为 dict，无法使用 Instructor，回退到 json_object 模式")
    fb_messages = inject_json_instruction(messages)
    create_kwargs: dict = {
        "model": model,
        "messages": fb_messages,
        "response_format": {"type": "json_object"},
    }
    if max_tokens is not None:
        create_kwargs["max_tokens"] = max_tokens
    response = await client.chat.completions.create(**create_kwargs)
    usage = getattr(response, "usage", None)
    choice = response.choices[0]
    text = choice.message.content or ""
    output_tokens = getattr(usage, "completion_tokens", None) if usage else None
    from lib.text_backends.base import warn_if_truncated

    warn_if_truncated(
        getattr(choice, "finish_reason", None),
        provider=provider,
        model=model,
        output_tokens=output_tokens,
    )
    return TextGenerationResult(
        text=text.strip() if isinstance(text, str) else str(text),
        provider=provider,
        model=model,
        input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
        output_tokens=output_tokens,
    )
