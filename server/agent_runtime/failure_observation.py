"""Agent failure observations: build a minimal shared envelope from explicit runtime/SDK evidence."""

from __future__ import annotations

import json
import traceback
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from claude_agent_sdk import CLIConnectionError, CLINotFoundError, ProcessError
from fastapi.encoders import jsonable_encoder

from lib.infra.logging_utils import sanitize_diagnostic_payload
from server.agent_runtime.turn_schema import infer_block_type


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
    """Let the standard library format the complete cause/context chain without modeling tracebacks again."""
    try:
        captured = traceback.TracebackException.from_exception(exc, capture_locals=False)
        return "".join(captured.format(chain=True))
    except Exception:
        return f"{type(exc).__module__}.{type(exc).__name__}: {_safe_text(exc)}"


# Derive conclusion keys only from structured evidence for frontend-localized one-line conclusions; do not parse error prose or build
# an ArcReel-specific upstream error taxonomy. Turn keys retain the SDK AssistantMessageError enum and
# original result subtype values; fall back to phase-specific generic keys when usable evidence is absent.
_SDK_ASSISTANT_ERROR_KEYS = frozenset(
    {"authentication_failed", "billing_error", "rate_limit", "invalid_request", "server_error"}
)
_RESULT_SUBTYPE_KEYS = frozenset({"error_max_turns", "error_max_budget_usd"})
TURN_FAILED_KEY = "turn_failed"
STARTUP_FAILED_KEY = "startup_failed"

# Startup exception class → key; list subclasses first (CLINotFoundError subclasses CLIConnectionError).
_STARTUP_EXCEPTION_KEYS: tuple[tuple[type[BaseException], str], ...] = (
    (CLINotFoundError, "cli_not_found"),
    (CLIConnectionError, "cli_connection_failed"),
    (ProcessError, "process_failed"),
    (TimeoutError, "startup_timeout"),
)


def _status_key(status: object) -> str | None:
    """When only an HTTP status is available, map it to the same values as SDK AssistantMessageError."""
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


# The SDK wraps all CLI startup exceptions in CLIConnectionError; a NotImplementedError cause means
# the current event loop cannot create subprocesses (uvicorn --reload uses SelectorEventLoop on Windows).
SUBPROCESS_UNSUPPORTED_KEY = "subprocess_unsupported"


def _cause_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and all(current is not seen for seen in chain):
        chain.append(current)
        current = current.__cause__
    return chain


def _startup_failure_key(exc: BaseException) -> str:
    """Find the first recognized exception class along the explicit cause chain."""
    chain = _cause_chain(exc)
    for index, current in enumerate(chain):
        connection_failed = isinstance(current, CLIConnectionError) and not isinstance(current, CLINotFoundError)
        if connection_failed and any(isinstance(cause, NotImplementedError) for cause in chain[index + 1 :]):
            return SUBPROCESS_UNSUPPORTED_KEY
        for exc_type, key in _STARTUP_EXCEPTION_KEYS:
            if isinstance(current, exc_type):
                return key
    return STARTUP_FAILED_KEY


def build_startup_failure_observation(
    exc: BaseException,
    *,
    project_name: str,
    session_id: str | None,
    sdk_stderr: str,
) -> dict[str, Any]:
    """Build a startup-failure observation; SDK stderr and the standard exception chain are the only raw evidence."""
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
            if isinstance(block, dict) and infer_block_type(block) == "text" and block.get("text") is not None
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
    """Build a turn observation from assistant/result messages already serialized by the SDK."""
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
