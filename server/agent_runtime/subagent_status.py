"""Persistent projection for independently running SDK subagents.

The parent Agent query can finish while async child agents keep appending to
the SDK SessionStore.  This module projects that durable transcript into a
small, reconnectable snapshot without tying child liveness to the parent turn
SSE.
"""

from __future__ import annotations

from typing import Any

from server.agent_runtime.event_log import SdkMessageNormalizer
from server.agent_runtime.usage_extraction import extract_text_token_usage

_AGENT_TOOL_NAMES = {"agent", "task"}
_TERMINAL_TASK_STATUSES = {"completed", "failed", "stopped", "cancelled", "interrupted", "killed", "stalled"}


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


def _merge_usage(current: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, Any] | None:
    if not incoming:
        return current
    merged = dict(current or {})
    for key in ("total_tokens", "tool_uses", "duration_ms"):
        value = incoming.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            continue
        previous = merged.get(key)
        merged[key] = max(previous, value) if isinstance(previous, int) else value
    return merged or None


def aggregate_subagent_usage(messages: list[dict[str, Any]]) -> dict[str, int] | None:
    """Sum durable per-call assistant usage, deduplicated by model message id.

    A single model response may be mirrored as multiple transcript rows (for
    example text first, then the tool-use block) with the same message id.  The
    last row carries the real usage while earlier rows commonly carry zeros, so
    each model message contributes only its largest observed token total.
    """
    by_message: dict[str, int] = {}
    saw_usage = False
    for index, message in enumerate(messages):
        if message.get("type") != "assistant":
            continue
        _input_tokens, _output_tokens, total_tokens = extract_text_token_usage(message)
        if total_tokens is None:
            continue
        saw_usage = True
        raw_id = message.get("message_id") or message.get("uuid")
        message_id = str(raw_id) if raw_id else f"row-{index}"
        by_message[message_id] = max(by_message.get(message_id, 0), total_tokens)
    if not saw_usage:
        return None
    return {"total_tokens": sum(by_message.values())}


def _find_task(
    tasks: dict[str, dict[str, Any]],
    *,
    tool_use_id: Any,
    task_id: Any,
) -> dict[str, Any] | None:
    if isinstance(tool_use_id, str) and tool_use_id:
        task = tasks.get(tool_use_id)
        if task is not None:
            return task
    if isinstance(task_id, str) and task_id:
        return next((task for task in tasks.values() if task.get("task_id") == task_id), None)
    return None


def build_subagent_snapshot(
    main_messages: list[dict[str, Any]],
    subagent_groups: dict[str, list[dict[str, Any]]],
    *,
    runtime_alive: bool | None = None,
    stalled_task_ids: set[str] | frozenset[str] | None = None,
    stall_timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Project main transcript anchors plus child transcripts into task cards.

    Transcript task notifications remain authoritative for normal terminal
    states.  If a task has no terminal notification but its owning runtime is
    definitively gone, project it as interrupted instead of inventing a
    perpetual running state from an old async-launch record.
    """
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
            raw_input = block.get("input")
            input_data: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
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
        task["status"] = (
            "stalled"
            if raw_status == "killed"
            else (raw_status if raw_status in _TERMINAL_TASK_STATUSES else "running")
        )

    # Task notifications are normalized at the single SDK-message semantic
    # boundary shared with the normal event log; no XML sniffing leaks into UI.
    normalizer = SdkMessageNormalizer(capture_failures=False)
    for message in main_messages:
        for entry in normalizer.normalize(message):
            if entry.get("type") != "system" or entry.get("subtype") not in {
                "task_started",
                "task_progress",
                "task_notification",
                "task_updated",
            }:
                continue
            tool_use_id = entry.get("tool_use_id")
            task_id = entry.get("task_id")
            task = _find_task(tasks, tool_use_id=tool_use_id, task_id=task_id)
            if task is None:
                continue
            if isinstance(task_id, str) and task_id:
                task["task_id"] = task_id
            status = str(entry.get("task_status") or "").strip().lower()
            if status:
                task["status"] = "stalled" if status == "killed" else status
                if status == "killed" and stall_timeout_seconds is not None:
                    task["stall_timeout_seconds"] = stall_timeout_seconds
            if entry.get("summary"):
                task["summary"] = str(entry["summary"])
            if isinstance(entry.get("usage"), dict):
                task["usage"] = _merge_usage(task.get("usage"), entry["usage"])

    for tool_use_id, messages in subagent_groups.items():
        task = tasks.get(tool_use_id)
        if task is not None:
            task["entries"] = _normalized_subagent_entries(messages)
            task["usage"] = _merge_usage(task.get("usage"), aggregate_subagent_usage(messages))

    ordered = list(tasks.values())
    stalled = stalled_task_ids or set()
    for task in ordered:
        task_id = task.get("task_id")
        if isinstance(task_id, str) and task_id in stalled:
            task["status"] = "stalled"
            task["stall_timeout_seconds"] = stall_timeout_seconds
    if runtime_alive is False:
        for task in ordered:
            if str(task.get("status") or "") not in _TERMINAL_TASK_STATUSES:
                task["status"] = "interrupted"
    return {
        "tasks": ordered,
        "active": any(str(task.get("status") or "") not in _TERMINAL_TASK_STATUSES for task in ordered),
    }


__all__ = ["build_subagent_snapshot"]
