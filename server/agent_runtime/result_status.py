"""Sole mapping from SDK result messages to terminal ArcReel session states."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from server.agent_runtime.models import SessionStatus

_SESSION_STATUSES = frozenset({"idle", "running", "completed", "error", "interrupted"})


def resolve_result_status(
    message: Mapping[str, Any],
    *,
    interrupt_requested: bool = False,
    preempted: bool = False,
) -> SessionStatus:
    """Map result to terminal session state; user interruption takes precedence over generic SDK errors.

    ``preempted``: an immediately sent message is not yet accepted. CLI still reports success for the interrupted round; identify it only through
    ``terminal_reason`` ``aborted_*``, recording interruption like a user stop.
    """
    explicit = str(message.get("session_status") or "").strip().lower()
    if explicit in _SESSION_STATUSES:
        return cast(SessionStatus, explicit)
    if preempted and str(message.get("terminal_reason") or "").startswith("aborted_"):
        return "interrupted"
    subtype = str(message.get("subtype") or "").strip().lower()
    is_error = bool(message.get("is_error")) or subtype.startswith("error")
    if interrupt_requested and (subtype in {"interrupted", "interrupt"} or is_error):
        return "interrupted"
    return "error" if is_error else "completed"


def result_indicates_error(message: Mapping[str, Any]) -> bool:
    """Determine failure only from SDK structured markers, without interpreting error reasons."""
    return resolve_result_status(message) == "error"
