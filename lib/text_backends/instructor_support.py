"""Instructor 降级支持 — 原生 json_schema 通道不可用时的结构化输出降级链。

链路为 TOOLS → MD_JSON：前者用 function calling 在 wire 层传 schema，后者把 schema 注入
prompt。只有 wire 层失败才继续降档，校验类失败即判终局（见 :func:`_is_tools_wire_failure`）。
OpenAI 兼容与 Ark 两个 backend 共用本模块，故降级链的调整在此一处生效。
"""

from __future__ import annotations

import logging

import instructor
from instructor import Mode
from instructor.core import IncompleteOutputException, InstructorRetryException, ResponseParsingError
from openai import BadRequestError
from pydantic import BaseModel

from lib.text_backends.base import (
    StructuredOutputExhaustedError,
    TextGenerationResult,
    TextOutputTruncatedError,
    TokenParam,
    check_truncation,
    truncate_for_log,
)

logger = logging.getLogger(__name__)

# 结构化输出降级链：TOOLS 用 function calling 传 schema，是 OpenAI 兼容端点里约束最强、
# 兼容面最广的一档；MD_JSON 纯靠 prompt 注入 schema，是最后兜底。native json_schema 档在
# 各 backend 的 generate() 里，本模块只负责 native 之后的两档（见 docs/adr/0014）。
_STRUCTURED_MODE_CHAIN: tuple[Mode, ...] = (Mode.TOOLS, Mode.MD_JSON)

# 上游不接受 tools 参数时的错误关键字。与 openai backend 的 _SCHEMA_ERROR_KEYWORDS 同构：
# 代理网关会把上游的参数不兼容包装成非 400 状态码，只认 BadRequestError 会漏判。
_TOOLS_UNSUPPORTED_KEYWORDS = (
    "tools",
    "tool_calls",
    "tool_choice",
    "function_call",
    "functions",
)


def _output_tokens_from_incomplete(exc: IncompleteOutputException) -> int | None:
    """尽力从截断异常携带的部分响应里取 output_tokens，取不到则 None（不阻断异常转换）。"""
    usage = getattr(getattr(exc, "last_completion", None), "usage", None)
    return getattr(usage, "completion_tokens", None) if usage else None


def _raw_output_from_exception(exc: BaseException) -> str:
    """尽力从 Instructor 异常里取模型这一轮的原始输出文本，取不到返回占位串。

    Instructor 把最后一次（失败的）completion 挂在异常上；不同档位的响应形态不同
    （MD_JSON 在 message.content、TOOLS 在 tool_calls 的 arguments），两处都取，
    供降级点的诊断日志说清「模型到底输出了什么」。
    """
    completion = getattr(exc, "last_completion", None)
    if completion is None:
        for attempt in getattr(exc, "failed_attempts", None) or []:
            completion = getattr(attempt, "completion", None)
            if completion is not None:
                break
    if completion is None:
        return "<无响应>"
    choices = getattr(completion, "choices", None) or []
    if not choices:
        return "<无响应>"
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if content:
        return truncate_for_log(str(content))
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        return truncate_for_log(str(getattr(getattr(tool_calls[0], "function", None), "arguments", None)))
    return "<无响应>"


def _failure_reason(exc: BaseException) -> str:
    """把某一档的失败压成一句可读原因，供 StructuredOutputExhaustedError 携带。"""
    if isinstance(exc, InstructorRetryException):
        attempts = getattr(exc, "failed_attempts", None) or []
        last = attempts[-1].exception if attempts else None
        detail = type(last).__name__ if last is not None else type(exc).__name__
        return f"降级链各档重试耗尽后模型输出仍不合规（{exc.n_attempts} 次尝试，最后一次 {detail}）"
    return f"降级链失败（{type(exc).__name__}: {exc}）"


def _is_tools_wire_failure(exc: BaseException) -> bool:
    """判断 TOOLS 档的失败是否属于 wire 层不兼容——只有这一类才继续降档到 MD_JSON。

    两种 wire 层形态：上游直接拒收 tools 参数（API 异常，Instructor 的档内重试只覆盖解析类
    异常，故这类异常原样冒泡），或上游收下了却不回 tool call（Instructor 解析器抛
    ``ResponseParsingError``，属可重试解析错误，档内重试耗尽后包在
    ``InstructorRetryException`` 里）。

    校验类失败（上游确实回了 tool call，只是参数结构反复不合规）不算 wire 层：换成约束更弱的
    MD_JSON 只会更差，直接判终局。其余异常（瞬态 5xx、连接错误等）也不算，原样冒泡交给调用方
    的 @with_retry_async 处理，不被降档吞掉。
    """
    if isinstance(exc, InstructorRetryException):
        return any(
            isinstance(attempt.exception, ResponseParsingError)
            for attempt in getattr(exc, "failed_attempts", None) or []
        )
    if isinstance(exc, TextOutputTruncatedError):
        return False
    if isinstance(exc, BadRequestError):
        return True
    return any(kw in str(exc) for kw in _TOOLS_UNSUPPORTED_KEYWORDS)


def generate_structured_via_instructor(
    client,
    model: str,
    messages: list[dict],
    response_model: type[BaseModel],
    mode: Mode = Mode.MD_JSON,
    max_retries: int = 2,
    max_tokens: int | None = None,
    token_param: TokenParam = "max_tokens",
    provider: str = "",
) -> tuple[str, int | None, int | None]:
    """通过 Instructor 生成结构化输出（同步版，供 Ark 等同步 SDK 使用）。

    token_param 决定 max_tokens 值在导线上的参数名，由调用方按端点选择。
    返回 (json_text, input_tokens, output_tokens)。Instructor 的
    ``IncompleteOutputException``（输出被 max_tokens 截断）归一为 :class:`TextOutputTruncatedError`，
    与原生结构化通道的截断行为同口径（见 docs/adr/0044）。
    """
    patched = instructor.from_openai(client, mode=mode)
    if patched is None:
        raise TypeError(
            f"instructor.from_openai() 返回 None — client 类型 {type(client).__name__} 不受支持，"
            "请传入 openai.OpenAI 或 openai.AsyncOpenAI 实例"
        )
    extra: dict = {token_param: max_tokens} if max_tokens is not None else {}
    try:
        result, completion = patched.chat.completions.create_with_completion(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            response_model=response_model,
            max_retries=max_retries,
            **extra,
        )
    except IncompleteOutputException as exc:
        raise TextOutputTruncatedError(
            provider=provider, model=model, output_tokens=_output_tokens_from_incomplete(exc)
        ) from exc
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
    token_param: TokenParam = "max_tokens",
    provider: str = "",
) -> tuple[str, int | None, int | None]:
    """通过 Instructor 生成结构化输出（异步版，供 OpenAI AsyncOpenAI 使用）。

    token_param 决定 max_tokens 值在导线上的参数名，由调用方按端点选择。
    返回 (json_text, input_tokens, output_tokens)。Instructor 的
    ``IncompleteOutputException``（输出被 max_tokens 截断）归一为 :class:`TextOutputTruncatedError`，
    与原生结构化通道的截断行为同口径（见 docs/adr/0044）。
    """
    patched = instructor.from_openai(client, mode=mode)
    if patched is None:
        raise TypeError(
            f"instructor.from_openai() 返回 None — client 类型 {type(client).__name__} 不受支持，"
            "请传入 openai.OpenAI 或 openai.AsyncOpenAI 实例"
        )
    extra: dict = {token_param: max_tokens} if max_tokens is not None else {}
    try:
        result, completion = await patched.chat.completions.create_with_completion(  # type: ignore[misc]
            model=model,
            messages=messages,  # type: ignore[arg-type]
            response_model=response_model,
            max_retries=max_retries,
            **extra,
        )
    except IncompleteOutputException as exc:
        raise TextOutputTruncatedError(
            provider=provider, model=model, output_tokens=_output_tokens_from_incomplete(exc)
        ) from exc
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


def _handle_mode_failure(
    exc: Exception,
    *,
    mode: Mode,
    next_mode: Mode | None,
    provider: str,
    model: str,
) -> None:
    """处理降级链某一档的失败：可降档则记日志后正常返回，否则抛终局异常或原样冒泡。

    正常返回即「调用方应继续下一档」。
    """
    if isinstance(exc, TextOutputTruncatedError):
        # 截断是可操作硬错误，重发同一份必然再截断的请求没有意义（docs/adr/0044）。
        raise exc
    if next_mode is not None and _is_tools_wire_failure(exc):
        logger.warning(
            "Instructor %s 档 wire 层不兼容（%s），降档到 %s 档；模型原始输出：%s",
            mode.value,
            exc,
            next_mode.value,
            _raw_output_from_exception(exc),
        )
        return
    if not isinstance(exc, InstructorRetryException):
        # 既非 wire 层不兼容也非档内重试耗尽（瞬态 5xx、连接错误、client 类型错误等），
        # 原样冒泡交给调用方的 @with_retry_async 判定，不误收敛成不可重试的终局异常。
        raise exc
    logger.warning(
        "Instructor %s 档重试耗尽，判定为结构化输出能力不足；模型原始输出：%s",
        mode.value,
        _raw_output_from_exception(exc),
    )
    raise StructuredOutputExhaustedError(provider=provider, model=model, reason=_failure_reason(exc)) from exc


def instructor_fallback_sync(
    client,
    model: str,
    messages: list[dict],
    response_schema: dict | type[BaseModel] | None,
    provider: str,
    max_tokens: int | None = None,
    token_param: TokenParam = "max_tokens",
):
    """同步 Instructor 降级路径。

    - response_schema 为 Pydantic 类 → TOOLS → MD_JSON 降级链
    - response_schema 为 dict → inject JSON instruction + json_object 模式

    供 Ark 等同步 SDK 后端使用（调用方用 asyncio.to_thread 包装）。
    不做瞬态重试，瞬态错误由调用方的重试循环统一处理；档内的结构化校验重试由 Instructor 自带。
    """
    if isinstance(response_schema, type):
        json_text: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        for index, mode in enumerate(_STRUCTURED_MODE_CHAIN):
            next_mode = _STRUCTURED_MODE_CHAIN[index + 1] if index + 1 < len(_STRUCTURED_MODE_CHAIN) else None
            try:
                json_text, input_tokens, output_tokens = generate_structured_via_instructor(
                    client=client,
                    model=model,
                    messages=messages,
                    response_model=response_schema,
                    mode=mode,
                    max_tokens=max_tokens,
                    token_param=token_param,
                    provider=provider,
                )
                break
            except Exception as exc:
                _handle_mode_failure(exc, mode=mode, next_mode=next_mode, provider=provider, model=model)
        assert json_text is not None  # 链内每条失败路径都抛异常，走到这里必有结果
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
        create_kwargs[token_param] = max_tokens
    response = client.chat.completions.create(**create_kwargs)
    usage = getattr(response, "usage", None)
    choice = response.choices[0]
    text = choice.message.content or ""
    output_tokens = getattr(usage, "completion_tokens", None) if usage else None
    # dict schema 仍是结构化输出诉求（response_schema 非空，只是无 Pydantic 模型可走原生
    # Instructor 通道），截断同样升级为硬错误。
    check_truncation(
        getattr(choice, "finish_reason", None),
        provider=provider,
        model=model,
        output_tokens=output_tokens,
        structured=True,
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
    response_schema: dict | type[BaseModel] | None,
    provider: str,
    max_tokens: int | None = None,
    token_param: TokenParam = "max_tokens",
):
    """异步 Instructor 降级路径。

    - response_schema 为 Pydantic 类 → TOOLS → MD_JSON 降级链 (async)
    - response_schema 为 dict → inject JSON instruction + json_object 模式 (async)

    供 OpenAI 等原生异步 SDK 后端使用。
    不做瞬态重试，瞬态错误由调用方的重试循环统一处理；档内的结构化校验重试由 Instructor 自带。
    """
    from lib.text_backends.base import TextGenerationResult

    if isinstance(response_schema, type):
        json_text: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        for index, mode in enumerate(_STRUCTURED_MODE_CHAIN):
            next_mode = _STRUCTURED_MODE_CHAIN[index + 1] if index + 1 < len(_STRUCTURED_MODE_CHAIN) else None
            try:
                json_text, input_tokens, output_tokens = await generate_structured_via_instructor_async(
                    client=client,
                    model=model,
                    messages=messages,
                    response_model=response_schema,
                    mode=mode,
                    max_tokens=max_tokens,
                    token_param=token_param,
                    provider=provider,
                )
                break
            except Exception as exc:
                _handle_mode_failure(exc, mode=mode, next_mode=next_mode, provider=provider, model=model)
        assert json_text is not None  # 链内每条失败路径都抛异常，走到这里必有结果
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
        create_kwargs[token_param] = max_tokens
    response = await client.chat.completions.create(**create_kwargs)
    usage = getattr(response, "usage", None)
    choice = response.choices[0]
    text = choice.message.content or ""
    output_tokens = getattr(usage, "completion_tokens", None) if usage else None
    # dict schema 仍是结构化输出诉求（response_schema 非空，只是无 Pydantic 模型可走原生
    # Instructor 通道），截断同样升级为硬错误。
    check_truncation(
        getattr(choice, "finish_reason", None),
        provider=provider,
        model=model,
        output_tokens=output_tokens,
        structured=True,
    )
    return TextGenerationResult(
        text=text.strip() if isinstance(text, str) else str(text),
        provider=provider,
        model=model,
        input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
        output_tokens=output_tokens,
    )
