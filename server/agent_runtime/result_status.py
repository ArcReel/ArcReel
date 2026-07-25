"""SDK result 消息到 ArcReel 会话终态的唯一映射。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from server.agent_runtime.models import SessionStatus

_SESSION_STATUSES = frozenset({"idle", "running", "completed", "error", "interrupted"})


def resolve_result_status(
    message: Mapping[str, Any],
    *,
    interrupt_requested: bool = False,
) -> SessionStatus:
    """把 result 映射为会话终态；用户中断优先于 SDK 的通用错误终态。"""
    explicit = str(message.get("session_status") or "").strip().lower()
    if explicit in _SESSION_STATUSES:
        return cast(SessionStatus, explicit)
    subtype = str(message.get("subtype") or "").strip().lower()
    is_error = bool(message.get("is_error")) or subtype.startswith("error")
    if interrupt_requested and (subtype in {"interrupted", "interrupt"} or is_error):
        return "interrupted"
    return "error" if is_error else "completed"


def result_indicates_error(message: Mapping[str, Any]) -> bool:
    """只根据 SDK 已给出的结构化标记判断是否为失败，不解释错误原因。"""
    return resolve_result_status(message) == "error"
