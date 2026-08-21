"""Persistent projection for independently running SDK subagents.

The parent Agent query can finish while async child agents keep appending to
the SDK SessionStore.  This module projects that durable transcript into a
small, reconnectable snapshot without tying child liveness to the parent turn
SSE.
"""

from __future__ import annotations

from typing import Any

from server.agent_runtime.event_log import SdkMessageNormalizer

_AGENT_TOOL_NAMES = {"agent", "task"}
_TERMINAL_TASK_STATUSES = {"completed", "failed", "stopped", "cancelled", "interrupted"}


def _content(message: dict[str, Any]) -> list[dict[str, Any]]:
    value = message.get("content")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _tool_result_id(message: dict[str, Any]) -> str | None:
    for block in _content(message):
        if block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
            return str(block["tool_use_id"])
    return None


def _normalized_subagent_entries(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalizer = SdkMessageNormalizer(capture_failures=False)
    entries: list[dict[str, Any]] = []
    for message in messages:
        for entry in normalizer.normalize(message):
            # The snapshot is a self-contained child timeline.  Local seq is
            # sufficient for the frontend projector and avoids colliding with
            # the parent event-log cursor.
            entry.pop("parent_tool_use_id", None)
            entries.append({"seq": len(entries), **entry})
    return entries


def build_subagent_snapshot(
    main_messages: list[dict[str, Any]],
    subagent_groups: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Project main transcript anchors plus child transcripts into task cards."""
    tasks: dict[str, dict[str, Any]] = {}

    for message in main_messages:
        if message.get("type") != "assistant":
            continue
        for block in _content(message):
            if block.get("type") != "tool_use":
                continue
            if str(block.get("name") or "").strip().lower() not in _AGENT_TOOL_NAMES:
                continue
            tool_use_id = block.get("id")
            if not isinstance(tool_use_id, str) or not tool_use_id:
                continue
            input_data = block.get("input") if isinstance(block.get("input"), dict) else {}
            tasks.setdefault(
                tool_use_id,
                {
                    "tool_use_id": tool_use_id,
                    "task_id": None,
                    "agent_type": str(input_data.get("subagent_type") or ""),
                    "description": str(input_data.get("description") or input_data.get("prompt") or ""),
                    "status": "running",
                    "summary": "",
                    "usage": None,
                    "entries": [],
                },
            )

    # Async launch metadata gives the durable child id and establishes that a
    # tool result means "running in background", not "completed".
    for message in main_messages:
        result = message.get("tool_use_result")
        if not isinstance(result, dict):
            continue
        agent_id = result.get("agentId")
        if not isinstance(agent_id, str) or not agent_id:
            continue
        tool_use_id = _tool_result_id(message)
        if not tool_use_id:
            continue
        task = tasks.setdefault(
            tool_use_id,
            {
                "tool_use_id": tool_use_id,
                "task_id": None,
                "agent_type": "",
                "description": str(result.get("description") or ""),
                "status": "running",
                "summary": "",
                "usage": None,
                "entries": [],
            },
        )
        task["task_id"] = agent_id
        raw_status = str(result.get("status") or "").strip().lower()
        task["status"] = raw_status if raw_status in _TERMINAL_TASK_STATUSES else "running"

    # Task notifications are normalized at the single SDK-message semantic
    # boundary shared with the normal event log; no XML sniffing leaks into UI.
    normalizer = SdkMessageNormalizer(capture_failures=False)
    for message in main_messages:
        for entry in normalizer.normalize(message):
            if entry.get("type") != "system" or entry.get("subtype") not in {
                "task_started",
                "task_progress",
                "task_notification",
            }:
                continue
            tool_use_id = entry.get("tool_use_id")
            if not isinstance(tool_use_id, str) or not tool_use_id:
                continue
            task = tasks.get(tool_use_id)
            if task is None:
                continue
            task_id = entry.get("task_id")
            if isinstance(task_id, str) and task_id:
                task["task_id"] = task_id
            status = str(entry.get("task_status") or "").strip().lower()
            if status:
                task["status"] = status
            if entry.get("summary"):
                task["summary"] = str(entry["summary"])
            if isinstance(entry.get("usage"), dict):
                task["usage"] = entry["usage"]

    for tool_use_id, messages in subagent_groups.items():
        task = tasks.get(tool_use_id)
        if task is not None:
            task["entries"] = _normalized_subagent_entries(messages)

    ordered = list(tasks.values())
    return {
        "tasks": ordered,
        "active": any(str(task.get("status") or "") not in _TERMINAL_TASK_STATUSES for task in ordered),
    }


__all__ = ["build_subagent_snapshot"]
