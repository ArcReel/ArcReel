"""Agent 故障观测的构造与安全序列化。"""

from __future__ import annotations

import base64
import json
import math
import re
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

_REDACTED = "••••"
_SENSITIVE_KEY_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9]*[_-])*"
    r"(?:api[_-]?key|authorization|cookie|password|passwd|pwd|secret|access[_-]?token|auth[_-]?token|"
    r"bearer[_-]?token|token)",
    re.IGNORECASE,
)
_COOKIE_LINE_RE = re.compile(r"(?im)^(\s*(?:set-)?cookie\s*:\s*).*$")
_AUTH_LINE_RE = re.compile(r"(?im)^(\s*(?:proxy-)?authorization\s*:\s*).*$")
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+")
_SENSITIVE_TEXT_KEY_PATTERN = (
    r"(?:[A-Za-z][A-Za-z0-9]*[_-])*"
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password|passwd|pwd|cookie|authorization)"
)
_DOUBLE_QUOTED_SECRET_RE = re.compile(
    rf"(?i)((?<![A-Za-z0-9]){_SENSITIVE_TEXT_KEY_PATTERN}\s*[\'\"]?\s*[=:]\s*\")"
    r'((?:\\.|[^"\\])*)(\")'
)
_SINGLE_QUOTED_SECRET_RE = re.compile(
    rf"(?i)((?<![A-Za-z0-9]){_SENSITIVE_TEXT_KEY_PATTERN}\s*['\"]?\s*[=:]\s*')"
    r"((?:\\.|[^'\\])*)(')"
)
_INLINE_SECRET_RE = re.compile(
    rf"(?i)((?<![A-Za-z0-9]){_SENSITIVE_TEXT_KEY_PATTERN}\s*[=:]\s*)"
    r"(?!['\"])([^\s,;&]+)"
)
_SIGNED_QUERY_RE = re.compile(
    r"(?i)([?&](?:x-amz-signature|x-goog-signature|signature|sig|access_token|auth_token|token|api_key|key|password)=)([^&#\s]*)"
)
_URL_PASSWORD_RE = re.compile(r"(?i)(https?://[^/@\s:]+:)([^@/\s]+)(@)")
_API_KEY_VALUE_RE = re.compile(r"(?<![A-Za-z0-9])sk-(?:ant-|proj-)?[A-Za-z0-9_-]{8,}")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _redact_text(value: str) -> str:
    """只替换可直接用于认证或签名的值，保留其余文本和排版。"""
    value = _COOKIE_LINE_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}", value)
    value = _AUTH_LINE_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}", value)
    value = _BEARER_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}", value)
    value = _DOUBLE_QUOTED_SECRET_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}{match.group(3)}", value)
    value = _SINGLE_QUOTED_SECRET_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}{match.group(3)}", value)
    value = _INLINE_SECRET_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}", value)
    value = _SIGNED_QUERY_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}", value)
    value = _URL_PASSWORD_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}{match.group(3)}", value)
    return _API_KEY_VALUE_RE.sub(_REDACTED, value)


def _safe_text(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return f"<unprintable {type(value).__module__}.{type(value).__name__}>"


def redact_failure_text(value: Any) -> str:
    """按故障观测的同一最小规则脱敏即将写入普通日志的文本。"""
    return _redact_text(_safe_text(value))


def _json_safe(value: Any, *, key_hint: str | None = None, stack: set[int] | None = None) -> Any:
    """无截断地转成 JSON 安全值，并按字段名与文本形态做最小脱敏。"""
    if key_hint and _SENSITIVE_KEY_RE.fullmatch(key_hint):
        return None if value is None else _REDACTED

    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, bytes | bytearray):
        raw = bytes(value)
        # 凭据通常以 ASCII 文本嵌在响应体中；surrogateescape 让无法解码的
        # 二进制字节可逆往返，同时仍能对可识别文本应用同一脱敏规则。
        decoded = raw.decode("utf-8", errors="surrogateescape")
        sanitized = _redact_text(decoded).encode("utf-8", errors="surrogateescape")
        return {
            "type": type(value).__name__,
            "encoding": "base64",
            "data": base64.b64encode(sanitized).decode("ascii"),
        }
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return _json_safe(value.value, key_hint=key_hint, stack=stack)

    active = stack if stack is not None else set()
    identity = id(value)
    if identity in active:
        return f"<cycle:{type(value).__name__}>"

    if isinstance(value, Mapping):
        active.add(identity)
        try:
            return {str(key): _json_safe(item, key_hint=str(key), stack=active) for key, item in value.items()}
        finally:
            active.remove(identity)

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        active.add(identity)
        try:
            return [_json_safe(item, stack=active) for item in value]
        finally:
            active.remove(identity)

    if isinstance(value, set | frozenset):
        active.add(identity)
        try:
            return [_json_safe(item, stack=active) for item in value]
        finally:
            active.remove(identity)

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump) and not isinstance(value, type):
        try:
            return _json_safe(model_dump(mode="python"), key_hint=key_hint, stack=active)
        except Exception:
            pass

    if is_dataclass(value) and not isinstance(value, type):
        try:
            return _json_safe(asdict(value), key_hint=key_hint, stack=active)
        except Exception:
            pass

    try:
        attributes = vars(value)
    except TypeError:
        attributes = None
    if attributes:
        active.add(identity)
        try:
            return {
                "type": type(value).__name__,
                "module": type(value).__module__,
                "attributes": _json_safe(attributes, stack=active),
            }
        finally:
            active.remove(identity)

    try:
        rendered = repr(value)
    except Exception:
        rendered = f"<unrepresentable {type(value).__module__}.{type(value).__name__}>"
    return _redact_text(rendered)


def _exception_chain(exc: BaseException) -> list[dict[str, Any]]:
    """按外层到根因的顺序保留异常类型、消息、堆栈与公开属性。"""
    chain: list[dict[str, Any]] = []
    current: BaseException | None = exc
    seen: set[int] = set()

    while current is not None and id(current) not in seen:
        seen.add(id(current))
        item: dict[str, Any] = {
            "type": type(current).__name__,
            "module": type(current).__module__,
            "message": _safe_text(current),
            "traceback": "".join(traceback.format_tb(current.__traceback__)),
        }
        item["args"] = list(current.args)
        attributes = {key: value for key, value in vars(current).items() if not key.startswith("_")}
        if attributes:
            item["attributes"] = attributes
        chain.append(item)

        if current.__cause__ is not None:
            current = current.__cause__
        elif current.__context__ is not None and not current.__suppress_context__:
            current = current.__context__
        else:
            current = None

    return chain


def build_startup_failure_observation(
    exc: BaseException,
    *,
    project_name: str,
    session_id: str | None,
    sdk_stderr: str,
) -> dict[str, Any]:
    """构造 Agent 启动失败观测；不把空 ``str(exc)`` 当成全部信息。"""
    chain = _exception_chain(exc)
    observed = chain[0]
    exception_message = observed["message"] or None
    source = "local_exception"
    message = exception_message
    if message is None and sdk_stderr:
        source = "sdk_stderr"
        message = sdk_stderr
    observation = {
        "version": 1,
        "phase": "startup",
        "timestamp": _utc_now_iso(),
        "project_name": project_name,
        "session_id": session_id,
        "summary": {
            "source": source,
            "type": observed["type"],
            "message": message,
        },
        "raw": {
            "exception_chain": chain,
            "sdk_stderr": sdk_stderr,
        },
    }
    safe = _json_safe(observation)
    assert isinstance(safe, dict)
    return safe


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
    """从 SDK 已实际发出的 assistant / result 故障对象构造轮次观测。"""
    source = "sdk_assistant" if assistant_message is not None else "sdk_result"

    observed_type: Any = None
    if assistant_message is not None:
        observed_type = assistant_message.get("error")
    if observed_type is None and result_message is not None:
        observed_type = result_message.get("subtype")

    status = result_message.get("api_error_status") if result_message is not None else None
    timestamp: Any = None
    if assistant_message is not None:
        timestamp = assistant_message.get("timestamp")
    if timestamp is None and result_message is not None:
        timestamp = result_message.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp:
        timestamp = _utc_now_iso()

    raw: dict[str, Any] = {}
    if assistant_message is not None:
        raw["assistant_message"] = dict(assistant_message)
    if result_message is not None:
        raw["result_message"] = dict(result_message)

    message = _message_text(assistant_message)
    if message is None:
        message = _message_text(result_message)

    observation = {
        "version": 1,
        "phase": "turn",
        "timestamp": timestamp,
        "project_name": project_name,
        "session_id": session_id,
        "summary": {
            "source": source,
            "type": observed_type,
            "status": status,
            "message": message,
        },
        "raw": raw,
    }
    safe = _json_safe(observation)
    assert isinstance(safe, dict)
    return safe


def build_turn_exception_failure_observation(
    exc: BaseException,
    *,
    project_name: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """构造 actor 等本地运行时异常导致的轮次故障观测。"""
    chain = _exception_chain(exc)
    observed = chain[0]
    observation = {
        "version": 1,
        "phase": "turn",
        "timestamp": _utc_now_iso(),
        "project_name": project_name,
        "session_id": session_id,
        "summary": {
            "source": "local_exception",
            "type": observed["type"],
            "message": observed["message"] or None,
        },
        "raw": {"exception_chain": chain},
    }
    safe = _json_safe(observation)
    assert isinstance(safe, dict)
    return safe


def failure_observation_json(observation: Mapping[str, Any]) -> str:
    """序列化已脱敏观测，供普通文本日志完整收录。"""
    return json.dumps(observation, ensure_ascii=False, sort_keys=True)
