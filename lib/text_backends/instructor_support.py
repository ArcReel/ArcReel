"""Instructor 降级支持 — 原生 json_schema 通道不可用时的结构化输出降级链。

链路为 TOOLS → MD_JSON：前者用 function calling 在 wire 层传 schema，后者把 schema 注入
prompt。只有 wire 层失败才继续降档，校验类失败即判终局（见 :func:`_classify_mode_failure`）。
OpenAI 兼容与 Ark 两个 backend 共用本模块，故降级链的调整在此一处生效。
"""

from __future__ import annotations

import logging
from enum import Enum

import instructor
from instructor import Mode
from instructor.core import IncompleteOutputException, InstructorRetryException, ResponseParsingError
from pydantic import BaseModel

from lib.text_backends.base import (
    StructuredOutputExhaustedError,
    TextGenerationResult,
    TextOutputTruncatedError,
    TokenParam,
    check_truncation,
    merge_billed_tokens,
    truncate_for_log,
)

logger = logging.getLogger(__name__)

# 结构化输出降级链：TOOLS 用 function calling 传 schema，是 OpenAI 兼容端点里约束最强、
# 兼容面最广的一档；MD_JSON 纯靠 prompt 注入 schema，是最后兜底。native json_schema 档在
# 各 backend 的 generate() 里，本模块只负责 native 之后的两档（见 docs/adr/0014）。
_STRUCTURED_MODE_CHAIN: tuple[Mode, ...] = (Mode.TOOLS, Mode.MD_JSON)

# 上游不接受 tools 参数时的错误关键字，匹配前把错误文本转小写（各家代理的大小写不统一）。
# 与 openai backend 的 _SCHEMA_ERROR_KEYWORDS 同构：代理网关会把上游的参数不兼容包装成非 400
# 状态码，只认 BadRequestError 会漏判。
_TOOLS_UNSUPPORTED_KEYWORDS = (
    "tools",
    "tool_calls",
    "tool_choice",
    "function_call",
    "functions",
)


def _mode_chain_steps() -> list[tuple[Mode, Mode | None]]:
    """降级链的 (当前档, 下一档) 序列；下一档为 None 表示已是末档、无处可退。"""
    return [
        (mode, _STRUCTURED_MODE_CHAIN[index + 1] if index + 1 < len(_STRUCTURED_MODE_CHAIN) else None)
        for index, mode in enumerate(_STRUCTURED_MODE_CHAIN)
    ]


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


def _api_call_failure(exc: InstructorRetryException) -> BaseException | None:
    """取这一档折在 API 调用上的原始异常；折在模型输出上则返回 None。

    Instructor 的档内重试只把解析 / 校验类异常记进 ``failed_attempts``；API 调用本身抛的
    异常会中断重试循环、被包进 ``InstructorRetryException``，原异常只挂在 ``__cause__`` 上。
    因此 ``failed_attempts`` 为空即表示这一档根本没走到解析——判据必须穿过这层包装取原异常，
    否则「上游拒收 tools 参数」永远认不出来。
    """
    if exc.failed_attempts:
        return None
    return exc.__cause__


def _failure_reason(exc: BaseException) -> str:
    """把某一档的失败压成一句可读原因，供 StructuredOutputExhaustedError 携带。"""
    if not isinstance(exc, InstructorRetryException):
        return f"降级链失败（{type(exc).__name__}: {exc}）"
    api_failure = _api_call_failure(exc)
    if api_failure is not None:
        return f"降级链各档传递 schema 的方式均被上游拒收（{api_failure}）"
    last = exc.failed_attempts[-1].exception if exc.failed_attempts else None
    detail = type(last).__name__ if last is not None else "未知原因"
    return f"降级链各档重试耗尽后模型输出仍不合规（{exc.n_attempts} 次尝试，最后一次 {detail}）"


def _billed_usage(exc: BaseException) -> tuple[int | None, int | None]:
    """取这一档已经计费的 token，供降档时并入最终结果。

    档内重试里每次拿到 HTTP 200 的尝试都已被计费，即便响应最终没通过解析 / 校验；Instructor
    把它们累加在 ``total_usage`` 上。上游直接拒收参数时该累加值为零，此时按「无计量」处理，
    以免把 None 塌成字面 0 token。
    """
    usage = getattr(exc, "total_usage", None)
    prompt = getattr(usage, "prompt_tokens", None) or None
    completion = getattr(usage, "completion_tokens", None) or None
    return prompt, completion


def _propagated_cause(exc: Exception) -> Exception:
    """冒泡时该抛的异常：API 调用失败抛原异常本身，其余抛原样。"""
    if isinstance(exc, InstructorRetryException):
        api_failure = _api_call_failure(exc)
        if isinstance(api_failure, Exception):
            return api_failure
    return exc


class _ModeFailure(Enum):
    """某一档失败后的处置。"""

    DOWNGRADE = "downgrade"
    """wire 层不兼容，换下一档还有机会。"""

    TERMINAL = "terminal"
    """模型输出反复不合规，换约束更弱的档只会更差。"""

    PROPAGATE = "propagate"
    """与结构化输出能力无关，交调用方判定。"""


def _classify_mode_failure(exc: BaseException) -> _ModeFailure:
    """判定某一档的失败该降档、判终局，还是原样冒泡。

    只有 wire 层不兼容才降档，两种形态：上游拒收 tools 参数（API 调用异常，须由错误文本指名
    tools / functions 才算数），或收下了却不回 tool call（解析器抛 ``ResponseParsingError``，
    属档内可重试解析错误）。

    API 调用异常一律走关键字判据，400 也不例外：无 ``STRUCTURED_OUTPUT`` 能力位的 Ark 模型
    不经原生档直接进本链，此处的 400 同样可能是模型名无效、上下文超限或策略拒绝。把这些无差别
    当成 tools 不兼容，既白花一次 MD_JSON 调用，又会把真实原因替换成不可重试的终局异常、
    使调用方拿到错误的处置指引。

    上游确实回了 tool call、只是参数结构反复不合规，属校验类失败，判终局。

    其余一律冒泡：瞬态 5xx 与连接错误交调用方的 ``@with_retry_async`` 判定，不被降级链吞成
    不可重试的终局异常；``TextOutputTruncatedError`` 是可操作硬错误，重发同一份必然再截断的
    请求没有意义（见 docs/adr/0044），它自身已是 NonRetryableError，原样传给调用方即可。
    """
    if not isinstance(exc, InstructorRetryException):
        return _ModeFailure.PROPAGATE
    api_failure = _api_call_failure(exc)
    if api_failure is None:
        if any(isinstance(attempt.exception, ResponseParsingError) for attempt in exc.failed_attempts or []):
            return _ModeFailure.DOWNGRADE
        return _ModeFailure.TERMINAL
    if any(kw in str(api_failure).lower() for kw in _TOOLS_UNSUPPORTED_KEYWORDS):
        return _ModeFailure.DOWNGRADE
    return _ModeFailure.PROPAGATE


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
) -> tuple[int | None, int | None]:
    """处理降级链某一档的失败：可降档则记日志后正常返回，否则抛终局异常或原样冒泡。

    正常返回即「调用方应继续下一档」，返回值是这一档已计费、需并入最终结果的 token。
    """
    failure = _classify_mode_failure(exc)
    if failure is _ModeFailure.PROPAGATE:
        # 冒泡时剥掉 Instructor 的包装：调用方的 @with_retry_async 先按异常类型判瞬态
        # （ConnectionError / TimeoutError），包着一层就只剩消息文本匹配，漏判连接类错误。
        raise _propagated_cause(exc)
    if failure is _ModeFailure.DOWNGRADE and next_mode is not None:
        logger.warning(
            "Instructor %s 档 wire 层不兼容（%s），降档到 %s 档；模型原始输出：%s",
            mode.value,
            exc,
            next_mode.value,
            _raw_output_from_exception(exc),
        )
        return _billed_usage(exc)
    # 校验类耗尽，或末档仍是 wire 层不兼容——降级链已无更弱的档可退。
    logger.warning(
        "Instructor %s 档失败且降级链已走完，判定为结构化输出能力不足；模型原始输出：%s",
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
        billed_input: int | None = None
        billed_output: int | None = None
        for mode, next_mode in _mode_chain_steps():
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
                discarded_input, discarded_output = _handle_mode_failure(
                    exc, mode=mode, next_mode=next_mode, provider=provider, model=model
                )
                billed_input = merge_billed_tokens(billed_input, discarded_input)
                billed_output = merge_billed_tokens(billed_output, discarded_output)
        assert json_text is not None  # 链内每条失败路径都抛异常，走到这里必有结果
        return TextGenerationResult(
            text=json_text,
            provider=provider,
            model=model,
            input_tokens=merge_billed_tokens(input_tokens, billed_input),
            output_tokens=merge_billed_tokens(output_tokens, billed_output),
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
    if isinstance(response_schema, type):
        json_text: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        billed_input: int | None = None
        billed_output: int | None = None
        for mode, next_mode in _mode_chain_steps():
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
                discarded_input, discarded_output = _handle_mode_failure(
                    exc, mode=mode, next_mode=next_mode, provider=provider, model=model
                )
                billed_input = merge_billed_tokens(billed_input, discarded_input)
                billed_output = merge_billed_tokens(billed_output, discarded_output)
        assert json_text is not None  # 链内每条失败路径都抛异常，走到这里必有结果
        return TextGenerationResult(
            text=json_text,
            provider=provider,
            model=model,
            input_tokens=merge_billed_tokens(input_tokens, billed_input),
            output_tokens=merge_billed_tokens(output_tokens, billed_output),
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
