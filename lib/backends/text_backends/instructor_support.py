"""Instructor fallback support: structured-output chains when native json_schema is unavailable.

TOOLS → MD_JSON: the former sends schemas over wire-level function calling, the latter injects them into
prompts. Only wire failures descend further; validation failures are terminal (:func:`_classify_mode_failure`).
OpenAI-compatible and Ark backends share this module, centralizing fallback-chain changes here.
"""

from __future__ import annotations

import logging
from enum import Enum
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any

import instructor
from instructor import Mode
from instructor.core import (
    AsyncValidationError,
    IncompleteOutputException,
    InstructorRetryException,
    ResponseParsingError,
)
from pydantic import BaseModel, ValidationError

from lib.backends.text_backends.base import (
    StructuredOutputExhaustedError,
    TextGenerationResult,
    TextOutputTruncatedError,
    TokenParam,
    chat_completion_result,
    check_truncation,
    merge_billed_usage,
    model_facing_response_model,
    strip_leading_think_block,
    truncate_for_log,
)

if TYPE_CHECKING:
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# Structured fallback: TOOLS sends schemas through function calling, the strongest and most broadly compatible
# OpenAI-compatible tier; MD_JSON prompt injection is the final fallback. Native json_schema lives in
# backend generate() methods; this module handles only the two postnative tiers (docs/adr/0014).
_STRUCTURED_MODE_CHAIN: tuple[Mode, ...] = (Mode.TOOLS, Mode.MD_JSON)

# Error keywords for upstream tools-parameter rejection; lowercase before matching because proxy capitalization varies.
# Like openai backend _SCHEMA_ERROR_KEYWORDS: gateways wrap upstream parameter incompatibility in non-400
# statuses, so BadRequestError alone misses failures.
_TOOLS_UNSUPPORTED_KEYWORDS = (
    "tools",
    "tool_calls",
    "tool_choice",
    "function_call",
    "functions",
)

# Parsing/validation exceptions terminating tiers, matching Instructor v2 retry loop _RETRYABLE_PARSE_ERRORS.
# Their terminating-cause types distinguish model-output failures from API failures; drift would break this distinction,
# so TestInstructorExceptionShape pins actual Instructor behavior.
_PARSE_FAILURE_TYPES: tuple[type[BaseException], ...] = (
    ValidationError,
    JSONDecodeError,
    AsyncValidationError,
    ResponseParsingError,
)


def _mode_chain_steps() -> list[tuple[Mode, Mode | None]]:
    """Fallback (current tier, next tier) pairs; None next tier means the last tier, with nowhere further to fall back."""
    return [
        (mode, _STRUCTURED_MODE_CHAIN[index + 1] if index + 1 < len(_STRUCTURED_MODE_CHAIN) else None)
        for index, mode in enumerate(_STRUCTURED_MODE_CHAIN)
    ]


def _output_tokens_from_incomplete(exc: IncompleteOutputException) -> int | None:
    """Best-effort output_tokens from partial responses on truncation exceptions; None if unavailable, without blocking exception conversion."""
    usage = getattr(getattr(exc, "last_completion", None), "usage", None)
    return getattr(usage, "completion_tokens", None) if usage else None


def _raw_output_from_exception(exc: BaseException) -> str:
    """Best-effort raw output text for this attempt from Instructor exceptions; return a placeholder if unavailable.

    Instructor attaches the final failed completion to exceptions; response shapes vary by tier
    (MD_JSON in message.content, TOOLS in tool_calls arguments). Read both locations
    for fallback diagnostics explaining what the model actually produced.
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
    """Get the original exception terminating this tier; None if termination occurred during output parsing/validation.

    Instructor attaches termination causes through ``__cause__`` (``raise ... from last_exception``); predicates must
    unwrap this layer to recognize upstream tools-parameter rejection.

    Nonempty ``failed_attempts`` is not a proxy: parsing/validation failures accumulate in ``failed_attempts`` without
    clearing. After one parsing failure followed by proxy 503, ``failed_attempts`` is nonempty but termination
    is 503. Using the former would misclassify transients as output noncompliance and swallow caller retries.
    :func:`_model_output_failure` classifies output-side termination from the terminating cause's type.
    """
    if exc.__cause__ is None or _model_output_failure(exc) is not None:
        return None
    return exc.__cause__


def _model_output_failure(exc: InstructorRetryException) -> BaseException | None:
    """Return parsing/validation exceptions when model output terminates this tier; None for API failures.

    Instructor attaches the terminating cause through ``__cause__``; for parsing/validation failures it is the last attempt's
    parsing exception. Missing tool calls in TOOLS responses count too: Instructor retries parsing failures with
    corrective reask prompts, terminating as ``ResponseParsingError`` only after within-tier exhaustion.

    This answers only whether output failed, not whether changing tiers helps: :func:`_tool_call_absent`
    decides tier changes from response structure.
    """
    cause = exc.__cause__
    return cause if isinstance(cause, _PARSE_FAILURE_TYPES) else None


def _first_choice_message(exc: BaseException | None) -> Any | None:
    """First choice message from raw responses attached to parsing exceptions; None without parsing exceptions, responses, or choices."""
    if not isinstance(exc, ResponseParsingError):
        return None
    choices = getattr(getattr(exc, "raw_response", None), "choices", None) or []
    if not choices:
        return None
    return getattr(choices[0], "message", None)


def _tool_call_absent(exc: BaseException | None) -> bool:
    """Responses contain no tool calls at all (wire incompatibility), rather than tool calls with unusable arguments.

    TOOLS ``ResponseParsingError`` has three causes: missing tool calls, missing tool-call arguments,
    or arguments not JSON-serializable. Only the first means this upstream channel produces no tool calls and merits changing tiers;
    the other two return actual tool calls with invalid content, a validation failure that weaker tiers would worsen.
    Inspect response structure, never error wording, so Instructor wording changes cannot break detection.
    """
    if not isinstance(exc, ResponseParsingError):
        return False
    message = _first_choice_message(exc)
    if getattr(message, "tool_calls", None):
        return False
    return getattr(message, "function_call", None) is None


def _failure_reason(exc: BaseException) -> str:
    """Reduce a tier failure to a readable reason carried by StructuredOutputExhaustedError."""
    if not isinstance(exc, InstructorRetryException):
        return f"降级链失败（{type(exc).__name__}: {exc}）"
    api_failure = _api_call_failure(exc)
    if api_failure is not None:
        return f"降级链各档传递 schema 的方式均被上游拒收（{api_failure}）"
    last = exc.failed_attempts[-1].exception if exc.failed_attempts else None
    detail = type(last).__name__ if last is not None else "未知原因"
    return f"降级链各档重试耗尽后模型输出仍不合规（{exc.n_attempts} 次尝试，最后一次 {detail}）"


def _billed_usage(exc: BaseException, *, provider: str, model: str) -> TextGenerationResult:
    """Get already-billed tier tokens for merging into final results on fallback.

    Every within-tier retry receiving HTTP 200 was billed, even if parsing/validation ultimately failed; Instructor
    accumulates these in ``total_usage``. Direct upstream parameter rejection has zero accumulated usage; treat this as unmeasured,
    avoiding collapsing None into literal zero tokens.
    """
    usage = getattr(exc, "total_usage", None)
    details = getattr(usage, "prompt_tokens_details", None)
    return TextGenerationResult(
        text="",
        provider=provider,
        model=model,
        input_tokens=getattr(usage, "prompt_tokens", None) or None,
        output_tokens=getattr(usage, "completion_tokens", None) or None,
        cache_read_tokens=getattr(details, "cached_tokens", None) or None,
    )


def _propagated_cause(exc: Exception) -> Exception:
    """Exception to propagate: original API-call failures themselves; otherwise preserve exceptions unchanged."""
    if isinstance(exc, InstructorRetryException):
        api_failure = _api_call_failure(exc)
        if isinstance(api_failure, Exception):
            return api_failure
    return exc


class _ModeFailure(Enum):
    """Disposition after a tier failure."""

    DOWNGRADE = "downgrade"
    """Wire-layer incompatibility; the next tier may still work."""

    TERMINAL = "terminal"
    """Repeatedly invalid model output; weaker constraints would only worsen it."""

    PROPAGATE = "propagate"
    """Unrelated to structured-output capability; let the caller decide."""


def _classify_mode_failure(exc: BaseException) -> _ModeFailure:
    """Classify tier failures as fallback, terminal, or unchanged propagation.

    Only wire incompatibility causes fallback: upstream tools-parameter rejection (API exceptions whose text explicitly names
    tools / functions), or acceptance without returning tool calls (:func:`_tool_call_absent`). For the latter,
    exhaust Instructor reask retries within the current tier first: corrective prompts can let occasionally tool-omitting models
    succeed under stronger constraints, at the cost of extra within-tier calls when upstream never produces tool calls.

    All API exceptions use keyword predicates, including 400: Ark models lacking ``STRUCTURED_OUTPUT`` bypass native
    tiers directly into this chain, so 400 may mean invalid model names, context limits, or policy rejection. Treating
    all as tools incompatibility wastes MD_JSON calls and replaces real causes with nonretryable terminal exceptions,
    giving callers incorrect handling guidance.

    Actual upstream tool calls with repeatedly invalid arguments are validation failures and terminal.

    Propagate everything else: transient 5xx/connection errors reach caller ``@with_retry_async`` predicates rather than being
    swallowed as nonretryable terminal failures. ``TextOutputTruncatedError`` is an actionable hard error: resending the same
    inevitably truncated request is pointless (docs/adr/0044); already NonRetryableError, it propagates unchanged.
    """
    if not isinstance(exc, InstructorRetryException):
        return _ModeFailure.PROPAGATE
    api_failure = _api_call_failure(exc)
    if api_failure is None:
        if _tool_call_absent(_model_output_failure(exc)):
            return _ModeFailure.DOWNGRADE
        return _ModeFailure.TERMINAL
    if any(kw in str(api_failure).lower() for kw in _TOOLS_UNSUPPORTED_KEYWORDS):
        return _ModeFailure.DOWNGRADE
    return _ModeFailure.PROPAGATE


def _strip_think_block_in_response(response: Any) -> None:
    """Instructor ``completion:response`` hook: strip leading thinking blocks from response bodies in place before parsing.

    Instructor MD_JSON takes the first parseable JSON in bodies; draft JSON inside thinking blocks could be mistaken for
    results. Rewrite ``message.content`` before parsing so parsing, reask, and ``failed_attempts`` all see
    only the body, matching native paths.
    """
    choices = getattr(response, "choices", None) or []
    if not choices:
        return
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if message is None or not isinstance(content, str):
        return
    stripped = strip_leading_think_block(content)
    if stripped != content:
        message.content = stripped


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
) -> TextGenerationResult:
    """Generate structured output through Instructor (synchronous, for Ark and other sync SDKs).

    token_param selects the wire parameter name carrying max_tokens, chosen by callers per endpoint.
    Results contain JSON text in ``text`` and this call's measurements. Instructor
    ``IncompleteOutputException`` (max_tokens truncation) normalizes to :class:`TextOutputTruncatedError`,
    matching native structured truncation behavior (docs/adr/0044).
    """
    patched = instructor.from_openai(client, mode=mode)
    patched.on("completion:response", _strip_think_block_in_response)
    extra: dict = {token_param: max_tokens} if max_tokens is not None else {}
    try:
        result, completion = patched.chat.completions.create_with_completion(
            model=model,
            messages=messages,  # type: ignore[arg-type]  # messages are ordinary dicts shared by OpenAI/Ark; instructor declares only OpenAI message TypedDicts.
            response_model=model_facing_response_model(response_model),
            max_retries=max_retries,
            **extra,
        )
    except IncompleteOutputException as exc:
        raise TextOutputTruncatedError(
            provider=provider, model=model, output_tokens=_output_tokens_from_incomplete(exc)
        ) from exc
    return chat_completion_result(text=result.model_dump_json(), provider=provider, model=model, usage=completion.usage)


async def generate_structured_via_instructor_async(
    client: AsyncOpenAI,
    model: str,
    messages: list[dict],
    response_model: type[BaseModel],
    mode: Mode = Mode.MD_JSON,
    max_retries: int = 2,
    max_tokens: int | None = None,
    token_param: TokenParam = "max_tokens",
    provider: str = "",
) -> TextGenerationResult:
    """Generate structured output through Instructor (asynchronous, for OpenAI AsyncOpenAI).

    token_param selects the wire parameter name carrying max_tokens, chosen by callers per endpoint.
    Results contain JSON text in ``text`` and this call's measurements. Instructor
    ``IncompleteOutputException`` (max_tokens truncation) normalizes to :class:`TextOutputTruncatedError`,
    matching native structured truncation behavior (docs/adr/0044).
    """
    patched = instructor.from_openai(client, mode=mode)
    patched.on("completion:response", _strip_think_block_in_response)
    extra: dict = {token_param: max_tokens} if max_tokens is not None else {}
    try:
        result, completion = await patched.chat.completions.create_with_completion(
            model=model,
            messages=messages,  # type: ignore[arg-type]  # messages are ordinary dicts shared by OpenAI/Ark; instructor declares only OpenAI message TypedDicts.
            response_model=model_facing_response_model(response_model),
            max_retries=max_retries,
            **extra,
        )
    except IncompleteOutputException as exc:
        raise TextOutputTruncatedError(
            provider=provider, model=model, output_tokens=_output_tokens_from_incomplete(exc)
        ) from exc
    return chat_completion_result(text=result.model_dump_json(), provider=provider, model=model, usage=completion.usage)


def inject_json_instruction(messages: list[dict]) -> list[dict]:
    """Inject JSON-format instructions into messages to enable json_object mode.

    OpenAI APIs require the "JSON" keyword in prompts for json_object mode.
    If messages already contain "JSON", return an unchanged copy.
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
) -> TextGenerationResult:
    """Handle tier failure: log and return normally for fallback, otherwise raise terminal errors or propagate unchanged.

    Normal return means callers should continue to the next tier; returned tokens are this tier's billed usage to merge into final results.
    """
    failure = _classify_mode_failure(exc)
    if failure is _ModeFailure.PROPAGATE:
        # Strip Instructor wrappers before propagation: caller @with_retry_async first identifies transients by exception types
        # (ConnectionError / TimeoutError); wrappers leave only text matching and miss connection errors.
        raise _propagated_cause(exc)
    if failure is _ModeFailure.DOWNGRADE and next_mode is not None:
        logger.warning(
            "Instructor %s 档 wire 层不兼容（%s），降档到 %s 档；模型原始输出：%s",
            mode.value,
            exc,
            next_mode.value,
            _raw_output_from_exception(exc),
        )
        return _billed_usage(exc, provider=provider, model=model)
    # Validation exhausted, or final tier remains wire-incompatible: no weaker tier remains.
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
    """Synchronous Instructor fallback.

    - Pydantic-class response_schema → TOOLS → MD_JSON chain
    - dict response_schema → inject JSON instruction + json_object mode

    For Ark and other sync SDK backends (callers wrap in asyncio.to_thread).
    No transient retries here; callers handle them centrally. Instructor provides within-tier structured-validation retries.
    """
    if isinstance(response_schema, type):
        result: TextGenerationResult | None = None
        billed = TextGenerationResult(text="", provider=provider, model=model)
        for mode, next_mode in _mode_chain_steps():
            try:
                result = generate_structured_via_instructor(
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
                discarded = _handle_mode_failure(exc, mode=mode, next_mode=next_mode, provider=provider, model=model)
                merge_billed_usage(billed, discarded)
        assert result is not None  # Every chain failure path raises; reaching here guarantees results.
        merge_billed_usage(billed, result)
        billed.text = result.text
        return billed

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
    content = choice.message.content or ""
    # Match native paths: leading embedded thinking blocks in content never enter results.
    text = strip_leading_think_block(content) if isinstance(content, str) else str(content)
    output_tokens = getattr(usage, "completion_tokens", None) if usage else None
    # dict schemas still request structured output (nonempty response_schema without Pydantic models for native
    # Instructor channels); truncation likewise becomes a hard error.
    check_truncation(
        getattr(choice, "finish_reason", None),
        provider=provider,
        model=model,
        output_tokens=output_tokens,
        structured=True,
    )
    return chat_completion_result(text=text.strip(), provider=provider, model=model, usage=usage)


async def instructor_fallback_async(
    client: AsyncOpenAI,
    model: str,
    messages: list[dict],
    response_schema: dict | type[BaseModel] | None,
    provider: str,
    max_tokens: int | None = None,
    token_param: TokenParam = "max_tokens",
):
    """Asynchronous Instructor fallback.

    - Pydantic-class response_schema → TOOLS → MD_JSON chain (async)
    - dict response_schema → inject JSON instruction + json_object mode (async)

    For OpenAI and other native async SDK backends.
    No transient retries here; callers handle them centrally. Instructor provides within-tier structured-validation retries.
    """
    if isinstance(response_schema, type):
        result: TextGenerationResult | None = None
        billed = TextGenerationResult(text="", provider=provider, model=model)
        for mode, next_mode in _mode_chain_steps():
            try:
                result = await generate_structured_via_instructor_async(
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
                discarded = _handle_mode_failure(exc, mode=mode, next_mode=next_mode, provider=provider, model=model)
                merge_billed_usage(billed, discarded)
        assert result is not None  # Every chain failure path raises; reaching here guarantees results.
        merge_billed_usage(billed, result)
        billed.text = result.text
        return billed

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
    content = choice.message.content or ""
    # Match native paths: leading embedded thinking blocks in content never enter results.
    text = strip_leading_think_block(content) if isinstance(content, str) else str(content)
    output_tokens = getattr(usage, "completion_tokens", None) if usage else None
    # dict schemas still request structured output (nonempty response_schema without Pydantic models for native
    # Instructor channels); truncation likewise becomes a hard error.
    check_truncation(
        getattr(choice, "finish_reason", None),
        provider=provider,
        model=model,
        output_tokens=output_tokens,
        structured=True,
    )
    return chat_completion_result(text=text.strip(), provider=provider, model=model, usage=usage)
