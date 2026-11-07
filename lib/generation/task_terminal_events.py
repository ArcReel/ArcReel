"""Task terminal → project-event conversion/publication.

Queue succeeded/failed/cancelled transitions reuse project events,
letting frontend observe completion without polling. ``project_change_hints`` batch buses share
``/projects/{name}/events/stream`` SSE with other changes, without new connections.

**Refresh signals**, not entity changes: ``important=False`` + ``focus=None``;
frontend reloads task/affected-canvas data without notifications/focus navigation.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from lib.project.project_change_hints import emit_project_change_batch

logger = logging.getLogger(__name__)

#: Terminal lifecycle statuses, matching frontend ``isTerminalStatus``.
TERMINAL_TASK_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})

#: Terminal-change entity_type distinct from project entity changes.
TASK_ENTITY_TYPE = "task"

_STATUS_ACTIONS: dict[str, str] = {
    "succeeded": "task_succeeded",
    "failed": "task_failed",
    "cancelled": "task_cancelled",
}


def build_task_terminal_change(event: dict[str, Any]) -> dict[str, Any] | None:
    """Convert terminal rows into project-change dicts; None for nonterminal statuses."""
    action = _STATUS_ACTIONS.get(str(event.get("status") or ""))
    if action is None:
        return None

    task_id = str(event.get("task_id") or "")
    if not task_id:
        return None

    return {
        "entity_type": TASK_ENTITY_TYPE,
        "action": action,
        "entity_id": task_id,
        # label is log/debug only, not notifications, important=False.
        "label": task_id,
        "focus": None,
        "important": False,
        # Frontend uses this to select canvas reloads, currently reference_video only.
        "task_type": str(event.get("task_type") or ""),
    }


def emit_task_terminal_events(events: Iterable[dict[str, Any]]) -> None:
    """Publish terminal events grouped by project.

    Never propagate publication failures: task states persisted, events optimize realtime with frontend polling fallback.
    """
    by_project: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        project_name = str(event.get("project_name") or "")
        if not project_name:
            continue
        change = build_task_terminal_change(event)
        if change is None:
            continue
        by_project.setdefault(project_name, []).append(change)

    for project_name, changes in by_project.items():
        try:
            emit_project_change_batch(project_name, changes)
        except Exception:
            logger.exception("发送任务终态项目事件失败 project=%s count=%d", project_name, len(changes))
