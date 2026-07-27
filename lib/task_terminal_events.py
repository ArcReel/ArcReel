"""任务终态 → 项目事件的转换与发布。

任务队列的终态迁移（succeeded / failed / cancelled）复用既有的项目事件通道推送，
让前端不必靠轮询才发现任务结束。事件走 ``project_change_hints`` 的 batch 总线，
与其它项目变更同一条 SSE（``/projects/{name}/events/stream``），不新增连接。

这些变更是**刷新信号**而非实体变更：``important=False`` + ``focus=None``，
前端据此不弹通知、不触发聚焦跳转，只用来重拉任务列表与受影响的画布数据。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from lib.project_change_hints import emit_project_change_batch

logger = logging.getLogger(__name__)

#: 任务生命周期末端状态。与前端 ``isTerminalStatus`` 同口径。
TERMINAL_TASK_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})

#: 任务终态变更的 entity_type，与项目实体变更区分开。
TASK_ENTITY_TYPE = "task"

_STATUS_ACTIONS: dict[str, str] = {
    "succeeded": "task_succeeded",
    "failed": "task_failed",
    "cancelled": "task_cancelled",
}


def build_task_terminal_change(event: dict[str, Any]) -> dict[str, Any] | None:
    """把一条终态记录转成项目变更 dict；状态非终态时返回 None。"""
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
        # label 不进通知文案（important=False），仅作日志/调试可读标识。
        "label": task_id,
        "focus": None,
        "important": False,
        "task_type": str(event.get("task_type") or ""),
        "resource_id": str(event.get("resource_id") or ""),
    }


def emit_task_terminal_events(events: Iterable[dict[str, Any]]) -> None:
    """按项目分组发布任务终态事件。

    发布失败不向上抛：任务状态本身已落库，事件只是实时性优化，前端轮询兜底。
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
