"""Agent 故障观测：从明确的 runtime/SDK 证据构造最小通用外壳。"""

from __future__ import annotations

import json
import traceback
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from claude_agent_sdk import CLIConnectionError, CLINotFoundError, ProcessError
from fastapi.encoders import jsonable_encoder

from lib.infra.logging_utils import sanitize_diagnostic_payload


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sanitize_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    encoded = jsonable_encoder(observation)
    sanitized = sanitize_diagnostic_payload(encoded)
    assert isinstance(sanitized, dict)
    return sanitized


def _safe_text(value: object) -> str:
    try:
        return str(value)
    except Exception:
        return f"<unprintable {type(value).__module__}.{type(value).__name__}>"


def _format_exception(exc: BaseException) -> str:
    """交给标准库格式化完整 cause/context 链，不重复建模 traceback。"""
    try:
        captured = traceback.TracebackException.from_exception(exc, capture_locals=False)
        return "".join(captured.format(chain=True))
    except Exception:
        return f"{type(exc).__module__}.{type(exc).__name__}: {_safe_text(exc)}"


# 结论 key 只从结构化证据派生，前端据此本地化一句话结论；不解析错误文案，不建
# ArcReel 自己的上游错误分类。轮次 key 沿用 SDK 的 AssistantMessageError 枚举与
# result subtype 原值，取不到可用证据时回落到按阶段的通用 key。
_SDK_ASSISTANT_ERROR_KEYS = frozenset(
    {"authentication_failed", "billing_error", "rate_limit", "invalid_request", "server_error"}
)
_RESULT_SUBTYPE_KEYS = frozenset({"error_max_turns", "error_max_budget_usd"})
TURN_FAILED_KEY = "turn_failed"
STARTUP_FAILED_KEY = "startup_failed"

# 启动异常类 → key；子类排在父类之前（CLINotFoundError 是 CLIConnectionError 的子类）。
_STARTUP_EXCEPTION_KEYS: tuple[tuple[type[BaseException], str], ...] = (
    (CLINotFoundError, "cli_not_found"),
    (CLIConnectionError, "cli_connection_failed"),
    (ProcessError, "process_failed"),
    (TimeoutError, "startup_timeout"),
)


def _status_key(status: object) -> str | None:
    """只有 HTTP 状态时，把它归到 SDK AssistantMessageError 的同一组取值。"""
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    if status == 401:
        return "authentication_failed"
    if status == 402:
        return "billing_error"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "server_error"
    if status >= 400:
        return "invalid_request"
    return None


def _turn_failure_key(assistant_error: object, result_subtype: object, status: object) -> str:
    if isinstance(assistant_error, str) and assistant_error in _SDK_ASSISTANT_ERROR_KEYS:
        return assistant_error
    if isinstance(result_subtype, str) and result_subtype in _RESULT_SUBTYPE_KEYS:
        return result_subtype
    return _status_key(status) or TURN_FAILED_KEY


def _startup_failure_key(exc: BaseException) -> str:
    """沿显式 cause 链找第一个可识别的异常类。"""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for exc_type, key in _STARTUP_EXCEPTION_KEYS:
            if isinstance(current, exc_type):
                return key
        current = current.__cause__
    return STARTUP_FAILED_KEY


def build_startup_failure_observation(
    exc: BaseException,
    *,
    project_name: str,
    session_id: str | None,
    sdk_stderr: str,
) -> dict[str, Any]:
    """构造启动失败观测；SDK stderr 与标准异常链是唯一原始证据。"""
    exception_message = _safe_text(exc) or None
    source = "local_exception"
    message = exception_message
    if message is None and sdk_stderr:
        source = "sdk_stderr"
        message = sdk_stderr
    return _sanitize_observation(
        {
            "version": 1,
            "phase": "startup",
            "timestamp": _utc_now_iso(),
            "project_name": project_name,
            "session_id": session_id,
            "summary": {
                "key": _startup_failure_key(exc),
                "source": source,
                "type": type(exc).__name__,
                "message": message,
            },
            "raw": {
                "exception": {
                    "type": type(exc).__name__,
                    "module": type(exc).__module__,
                    "message": exception_message,
                    "traceback": _format_exception(exc),
                },
                "sdk_stderr": sdk_stderr,
            },
        }
    )


def _message_text(message: Mapping[str, Any] | None) -> str | None:
    if message is None:
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        texts = [
            str(block.get("text"))
            for block in content
            if isinstance(block, Mapping) and block.get("type") == "text" and block.get("text") is not None
        ]
        if texts:
            return "\n".join(texts)
    result = message.get("result")
    if isinstance(result, str) and result:
        return result
    errors = message.get("errors")
    if isinstance(errors, list):
        rendered = [str(item) for item in errors if item is not None]
        if rendered:
            return "\n".join(rendered)
    return None


def build_turn_failure_observation(
    *,
    assistant_message: Mapping[str, Any] | None,
    result_message: Mapping[str, Any] | None,
    project_name: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """从 SDK 已序列化的 assistant/result 消息构造轮次观测。"""
    source = "sdk_assistant" if assistant_message is not None else "sdk_result"
    observed_type: Any = assistant_message.get("error") if assistant_message is not None else None
    if observed_type is None and result_message is not None:
        observed_type = result_message.get("subtype")
    status = result_message.get("api_error_status") if result_message is not None else None
    result_subtype = result_message.get("subtype") if result_message is not None else None
    assistant_error = assistant_message.get("error") if assistant_message is not None else None
    timestamp: Any = assistant_message.get("timestamp") if assistant_message is not None else None
    if timestamp is None and result_message is not None:
        timestamp = result_message.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp:
        timestamp = _utc_now_iso()

    raw: dict[str, Any] = {}
    if assistant_message is not None:
        raw["assistant_message"] = dict(assistant_message)
    if result_message is not None:
        raw["result_message"] = dict(result_message)

    return _sanitize_observation(
        {
            "version": 1,
            "phase": "turn",
            "timestamp": timestamp,
            "project_name": project_name,
            "session_id": session_id,
            "summary": {
                "key": _turn_failure_key(assistant_error, result_subtype, status),
                "source": source,
                "type": observed_type,
                "status": status,
                "message": _message_text(assistant_message) or _message_text(result_message),
            },
            "raw": raw,
        }
    )


def failure_observation_json(observation: Mapping[str, Any]) -> str:
    return json.dumps(observation, ensure_ascii=False, sort_keys=True)
