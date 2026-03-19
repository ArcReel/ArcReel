"""
Conversation turn grouping shared by history loading and live SSE streaming.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Optional

from server.agent_runtime.turn_schema import (
    infer_block_type,
    normalize_block as _normalize_block,
    normalize_content as _normalize_content,
    normalize_turn,
)

# Constants for skill content detection
_SKILL_BASE_DIR_PREFIX = "Base directory for this skill:"
_SKILL_CONTENT_PREFIX = "Skill content:"
_SKILL_PATH_MARKER = ".claude/skills/"
_SKILL_FILE_MARKER = "SKILL.md"

# Metadata keys that indicate a user payload is system/subagent injected.
_SUBAGENT_PARENT_KEYS = (
    "parent_tool_use_id",
    "parentToolUseID",
    "parentToolUseId",
)
_SUBAGENT_CONTEXT_KEYS = (
    "sourceToolAssistantUUID",
    "source_tool_assistant_uuid",
    "toolUseResult",
    "tool_use_result",
    "agentId",
    "agent_id",
)
_SUBAGENT_BOOLEAN_KEYS = ("isSidechain", "is_sidechain")


# Regex for SDK-injected task notification user messages.
_TASK_NOTIFICATION_RE = re.compile(
    r"<task-notification>\s*.*?</task-notification>", re.DOTALL
)

# Pattern for CLI-injected interrupt echo messages.
# The exact text is an internal CLI implementation detail (not a stable API),
# so we use a loose prefix match rather than exact string comparison.
_INTERRUPT_ECHO_PREFIX = "[Request interrupted"


def _extract_task_notification(content: Any) -> Optional[dict[str, str]]:
    """Extract task notification fields from SDK-injected user message.

    The SDK injects task completion/failure notifications as plain user
    messages with ``<task-notification>`` XML.  This helper detects them
    and returns parsed fields, or *None* if the content is not a task
    notification.
    """
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif isinstance(block, str):
                texts.append(block)
        text = "\n".join(texts)
    elif isinstance(content, str):
        text = content
    else:
        return None

    match = _TASK_NOTIFICATION_RE.search(text)
    if not match:
        return None

    xml = match.group(0)

    def _tag(name: str) -> str:
        m = re.search(rf"<{name}>(.*?)</{name}>", xml, re.DOTALL)
        return m.group(1).strip() if m else ""

    return {
        "task_id": _tag("task-id"),
        "tool_use_id": _tag("tool-use-id"),
        "status": _tag("status"),
        "summary": _tag("summary"),
        "output_file": _tag("output-file"),
    }


def _is_skill_content_text(text: str) -> bool:
    """Check if text is system-injected skill content."""
    return (
        text.startswith(_SKILL_BASE_DIR_PREFIX)
        or text.startswith(_SKILL_CONTENT_PREFIX)
    )




def _is_tool_result_block(block: Any) -> bool:
    """
    Check if a content block is a tool result payload.

    Claude SDK tool result payloads may come in two shapes:
    1) {"type": "tool_result", "tool_use_id": "...", "content": "..."}
    2) {"tool_use_id": "...", "content": "...", "is_error": false}  # no explicit type
    """
    if not isinstance(block, dict):
        return False

    return infer_block_type(block) == "tool_result"


def _normalize_tool_result_block(block: dict[str, Any]) -> dict[str, Any]:
    """Normalize tool_result payload to canonical shape."""
    return {
        "type": "tool_result",
        "tool_use_id": block.get("tool_use_id"),
        "content": block.get("content", ""),
        "is_error": block.get("is_error", False),
    }



def _all_blocks_are_system_injected(blocks: list[Any]) -> bool:
    """Check whether all blocks are tool_result / skill content blocks."""
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if _is_tool_result_block(block):
            continue
        block_type = infer_block_type(block)
        if block_type == "text":
            text = block.get("text", "").strip()
            if _is_skill_content_text(text):
                continue
            return False
        return False
    return True


def _is_interrupt_echo(content: Any) -> bool:
    """Detect CLI-injected interrupt echo user message.

    When the user interrupts a running tool, the CLI injects a user message
    like ``[Request interrupted by user for tool use]``.  The exact wording
    is an internal CLI implementation detail, so we match by prefix.
    """
    text = ""
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        blocks = _normalize_content(content)
        if len(blocks) == 1 and blocks[0].get("type") == "text":
            text = (blocks[0].get("text") or "").strip()
    return text.startswith(_INTERRUPT_ECHO_PREFIX)


def _is_system_injected_user_message(content: Any) -> bool:
    """Check whether a user message is SDK-injected system payload."""
    if isinstance(content, str):
        return _is_skill_content_text(content.strip())
    if isinstance(content, list):
        return _all_blocks_are_system_injected(_normalize_content(content))
    return False


def _has_subagent_user_metadata(message: dict[str, Any]) -> bool:
    """Check whether a user message carries subagent/system metadata."""
    for key in _SUBAGENT_PARENT_KEYS:
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return True

    for key in _SUBAGENT_BOOLEAN_KEYS:
        if bool(message.get(key)):
            return True

    for key in _SUBAGENT_CONTEXT_KEYS:
        if key not in message:
            continue
        value = message.get(key)
        if value in (None, "", [], {}):
            continue
        return True

    return False


def _attach_tool_result(
    block: dict[str, Any],
    turn_content: list[dict[str, Any]],
    tool_use_map: dict[str, bool],
) -> None:
    """Attach tool_result block to corresponding tool_use when possible."""
    normalized = _normalize_tool_result_block(block)
    tool_use_id = normalized.get("tool_use_id")
    if tool_use_id and tool_use_id in tool_use_map:
        for existing_block in turn_content:
            if (
                isinstance(existing_block, dict)
                and existing_block.get("type") == "tool_use"
                and existing_block.get("id") == tool_use_id
            ):
                existing_block["result"] = normalized.get("content", "")
                existing_block["is_error"] = normalized.get("is_error", False)
                return
    turn_content.append(normalized)


def _attach_text_block(block: dict[str, Any], turn_content: list[dict[str, Any]]) -> None:
    """Attach text block, treating skill content specially."""
    text = block.get("text", "").strip()
    if _is_skill_content_text(text):
        for existing_block in reversed(turn_content):
            if (
                isinstance(existing_block, dict)
                and existing_block.get("type") == "tool_use"
                and existing_block.get("name") == "Skill"
            ):
                existing_block["skill_content"] = text
                return
        turn_content.append({"type": "skill_content", "text": text})
        return

    turn_content.append(block)


def _filter_system_blocks(
    content: Any,
    suppress_plain_text: bool = False,
) -> list[dict[str, Any]]:
    """
    Normalize/filter system-injected blocks before attachment.

    For subagent-injected payloads we suppress plain text blocks, because they
    often contain internal subagent prompts/telemetry and should not be rendered.
    """
    blocks = _normalize_content(content)
    filtered: list[dict[str, Any]] = []

    for block in blocks:
        if not isinstance(block, dict):
            continue

        if _is_tool_result_block(block):
            filtered.append(block)
            continue

        block_type = block.get("type", "")
        if block_type == "text":
            text = block.get("text", "").strip()
            if not text:
                continue
            if suppress_plain_text and not _is_skill_content_text(text):
                continue
            filtered.append(block)
            continue

        filtered.append(block)

    return filtered


def _attach_system_content_to_turn(
    turn: dict[str, Any],
    blocks: list[dict[str, Any]],
    tool_use_map: dict[str, bool],
) -> None:
    """Attach system-injected user content to current assistant turn."""
    turn_content = turn.get("content", [])

    for block in blocks:
        if not isinstance(block, dict):
            continue

        block_type = block.get("type", "")
        if _is_tool_result_block(block):
            _attach_tool_result(block, turn_content, tool_use_map)
        elif block_type == "text":
            _attach_text_block(block, turn_content)
        else:
            turn_content.append(block)


def _track_tool_uses(
    new_blocks: list[dict[str, Any]],
    tool_use_map: dict[str, bool],
) -> None:
    """Track tool_use IDs for later tool_result pairing."""
    for block in new_blocks:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool_id = block.get("id")
            if tool_id:
                tool_use_map[tool_id] = True


def _find_task_block(
    turn: Optional[dict[str, Any]], task_id: str
) -> Optional[dict[str, Any]]:
    """Find an existing task_progress block by task_id within a turn."""
    if not isinstance(turn, dict):
        return None
    content = turn.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if (
            isinstance(block, dict)
            and block.get("type") == "task_progress"
            and block.get("task_id") == task_id
        ):
            return block
    return None


def _resolve_stale_task_blocks(turns: list[dict[str, Any]]) -> None:
    """Auto-complete task_started blocks whose Agent tool_use already has a result.

    When the SDK doesn't emit TaskNotificationMessage, we infer task completion
    from the Agent tool_use having a populated result.
    """
    for turn in turns:
        content = turn.get("content")
        if not isinstance(content, list):
            continue

        # Build set of tool_use IDs that have results
        completed_tool_ids: set[str] = set()
        for block in content:
            if not isinstance(block, dict):
                continue
            if (
                block.get("type") == "tool_use"
                and block.get("name") == "Agent"
                and block.get("result") is not None
            ):
                tool_id = block.get("id")
                if tool_id:
                    completed_tool_ids.add(tool_id)

        if not completed_tool_ids:
            continue

        # Update stale task_progress blocks
        for block in content:
            if not isinstance(block, dict):
                continue
            if (
                block.get("type") == "task_progress"
                and block.get("status") == "task_started"
                and block.get("tool_use_id") in completed_tool_ids
            ):
                block["status"] = "task_notification"
                block["task_status"] = "completed"


def group_messages_into_turns(raw_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Group raw user/assistant/result messages into UI turns.

    Rules:
    - Consecutive assistant messages are merged.
    - tool_result blocks are attached to matching tool_use.
    - Skill content is attached to the most recent Skill tool_use block.
    """
    if not raw_messages:
        return []

    turns: list[dict[str, Any]] = []
    current_turn: Optional[dict[str, Any]] = None
    tool_use_map: dict[str, bool] = {}

    for msg in raw_messages:
        msg_type = msg.get("type", "")

        if msg_type == "result":
            if current_turn:
                turns.append(current_turn)
                current_turn = None
            continue  # Don't create independent result turn

        if msg_type == "user":
            content = msg.get("content", "")

            # SDK injects task notifications as user messages in the
            # transcript; convert to task_progress blocks so they render
            # identically to live TaskNotificationMessage events.
            task_info = _extract_task_notification(content)
            if task_info is not None:
                task_id = task_info["task_id"]
                task_block = {
                    "type": "task_progress",
                    "task_id": task_id,
                    "status": "task_notification",
                    "description": "",
                    "summary": task_info["summary"] or None,
                    "task_status": task_info["status"] or None,
                    "tool_use_id": task_info["tool_use_id"] or None,
                }
                if task_id:
                    existing = (
                        _find_task_block(current_turn, task_id)
                        if current_turn
                        else None
                    )
                    if existing is not None:
                        existing["status"] = "task_notification"
                        if task_block.get("summary"):
                            existing["summary"] = task_block["summary"]
                        if task_block.get("task_status"):
                            existing["task_status"] = task_block["task_status"]
                        continue
                if current_turn and current_turn.get("type") == "assistant":
                    current_turn.get("content", []).append(task_block)
                else:
                    if current_turn:
                        turns.append(current_turn)
                    current_turn = {
                        "type": "system",
                        "content": [task_block],
                        "uuid": msg.get("uuid"),
                        "timestamp": msg.get("timestamp"),
                    }
                continue

            # CLI-injected interrupt echo → convert to a system indicator
            if _is_interrupt_echo(content):
                if current_turn:
                    turns.append(current_turn)
                current_turn = {
                    "type": "system",
                    "content": [{
                        "type": "interrupt_notice",
                    }],
                    "uuid": msg.get("uuid"),
                    "timestamp": msg.get("timestamp"),
                }
                continue

            has_subagent_metadata = _has_subagent_user_metadata(msg)
            is_system_injected = (
                _is_system_injected_user_message(content)
                or has_subagent_metadata
            )
            if is_system_injected:
                filtered_blocks = _filter_system_blocks(
                    content,
                    suppress_plain_text=has_subagent_metadata,
                )
                if not filtered_blocks:
                    continue

                if current_turn and current_turn.get("type") == "assistant":
                    _attach_system_content_to_turn(
                        current_turn, filtered_blocks, tool_use_map
                    )
                else:
                    if current_turn:
                        turns.append(current_turn)
                    current_turn = {
                        "type": "system",
                        "content": filtered_blocks,
                        "uuid": msg.get("uuid"),
                        "timestamp": msg.get("timestamp"),
                    }
                continue

            if current_turn:
                turns.append(current_turn)
            current_turn = {
                "type": "user",
                "content": _normalize_content(content),
                "uuid": msg.get("uuid"),
                "timestamp": msg.get("timestamp"),
            }
            continue

        if msg_type == "assistant":
            new_blocks = _normalize_content(msg.get("content", []))
            _track_tool_uses(new_blocks, tool_use_map)

            if current_turn and current_turn.get("type") == "assistant":
                current_turn.get("content", []).extend(new_blocks)
            else:
                if current_turn:
                    turns.append(current_turn)
                current_turn = {
                    "type": "assistant",
                    "content": new_blocks,
                    "uuid": msg.get("uuid"),
                    "timestamp": msg.get("timestamp"),
                }
            continue

        if msg_type == "system":
            subtype = msg.get("subtype", "")
            if subtype in ("task_started", "task_progress", "task_notification"):
                task_id = msg.get("task_id")
                task_block = {
                    "type": "task_progress",
                    "task_id": task_id,
                    "status": subtype,
                    "description": msg.get("description", ""),
                    "summary": msg.get("summary"),
                    "task_status": msg.get("status"),
                    "usage": msg.get("usage"),
                    "tool_use_id": msg.get("tool_use_id"),
                }

                # For notification/progress updates, try to update existing block
                if subtype in ("task_notification", "task_progress") and task_id:
                    existing = _find_task_block(current_turn, task_id) if current_turn else None
                    if existing is not None:
                        existing["status"] = subtype
                        if task_block.get("summary"):
                            existing["summary"] = task_block["summary"]
                        if task_block.get("task_status"):
                            existing["task_status"] = task_block["task_status"]
                        if task_block.get("usage"):
                            existing["usage"] = task_block["usage"]
                        continue

                if current_turn and current_turn.get("type") == "assistant":
                    current_turn.get("content", []).append(task_block)
                else:
                    if current_turn:
                        turns.append(current_turn)
                    current_turn = {
                        "type": "system",
                        "content": [task_block],
                        "uuid": msg.get("uuid"),
                        "timestamp": msg.get("timestamp"),
                    }
                continue
            continue  # Ignore other system subtypes

        # Ignore other message types (stream_event/progress/etc)
        continue

    if current_turn:
        turns.append(current_turn)

    _resolve_stale_task_blocks(turns)

    return [normalize_turn(t) for t in turns]


def build_turn_patch(
    previous_turns: list[dict[str, Any]],
    current_turns: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Build minimal patch between two turn snapshots."""
    prev = previous_turns or []
    curr = current_turns or []

    if prev == curr:
        return None

    if len(curr) == len(prev) + 1 and curr[:-1] == prev:
        return {"op": "append", "turn": curr[-1]}

    if (
        len(curr) == len(prev)
        and len(curr) > 0
        and curr[:-1] == prev[:-1]
        and curr[-1] != prev[-1]
    ):
        return {"op": "replace_last", "turn": curr[-1]}

    return {"op": "reset", "turns": curr}
