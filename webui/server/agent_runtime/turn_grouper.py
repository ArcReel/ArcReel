"""
Conversation turn grouping shared by history loading and live SSE streaming.
"""

from __future__ import annotations

import copy
from typing import Any, Optional

# Constants for skill content detection
_SKILL_BASE_DIR_PREFIX = "Base directory for this skill:"
_SKILL_CONTENT_PREFIX = "Skill content:"
_SKILL_PATH_MARKER = ".claude/skills/"
_SKILL_FILE_MARKER = "SKILL.md"


def _is_skill_content_text(text: str) -> bool:
    """Check if text is system-injected skill content."""
    return (
        text.startswith(_SKILL_BASE_DIR_PREFIX)
        or text.startswith(_SKILL_CONTENT_PREFIX)
        or (_SKILL_PATH_MARKER in text and _SKILL_FILE_MARKER in text)
    )


def _infer_block_type(block: dict[str, Any]) -> str:
    """Infer content block type when SDK omits explicit `type`."""
    explicit_type = block.get("type")
    if isinstance(explicit_type, str) and explicit_type:
        return explicit_type

    if block.get("tool_use_id") and ("content" in block or "is_error" in block):
        return "tool_result"

    if block.get("id") and block.get("name") and "input" in block:
        return "tool_use"

    if "text" in block:
        return "text"

    return ""


def _normalize_block(block: Any) -> Any:
    """Normalize a single content block to include inferred type when possible."""
    if not isinstance(block, dict):
        return copy.deepcopy(block)

    normalized: dict[str, Any] = copy.deepcopy(block)
    block_type = _infer_block_type(normalized)
    if block_type and not normalized.get("type"):
        normalized["type"] = block_type
    return normalized


def _is_tool_result_block(block: Any) -> bool:
    """
    Check if a content block is a tool result payload.

    Claude SDK tool result payloads may come in two shapes:
    1) {"type": "tool_result", "tool_use_id": "...", "content": "..."}
    2) {"tool_use_id": "...", "content": "...", "is_error": false}  # no explicit type
    """
    if not isinstance(block, dict):
        return False

    return _infer_block_type(block) == "tool_result"


def _normalize_tool_result_block(block: dict[str, Any]) -> dict[str, Any]:
    """Normalize tool_result payload to canonical shape."""
    return {
        "type": "tool_result",
        "tool_use_id": block.get("tool_use_id"),
        "content": block.get("content", ""),
        "is_error": block.get("is_error", False),
    }


def _normalize_content(content: Any) -> list[dict[str, Any]]:
    """Normalize message content into content blocks."""
    if isinstance(content, str):
        if not content.strip():
            return []
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        normalized_blocks: list[dict[str, Any]] = []
        for block in content:
            normalized = _normalize_block(block)
            if isinstance(normalized, dict):
                normalized_blocks.append(normalized)
        return normalized_blocks
    return []


def _all_blocks_are_system_injected(blocks: list[Any]) -> bool:
    """Check whether all blocks are tool_result / skill content blocks."""
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if _is_tool_result_block(block):
            continue
        block_type = _infer_block_type(block)
        if block_type == "text":
            text = block.get("text", "").strip()
            if _is_skill_content_text(text):
                continue
            return False
        return False
    return True


def _is_system_injected_user_message(content: Any) -> bool:
    """Check whether a user message is SDK-injected system payload."""
    if isinstance(content, str):
        return _is_skill_content_text(content.strip())
    if isinstance(content, list):
        return _all_blocks_are_system_injected(_normalize_content(content))
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


def _attach_system_content_to_turn(
    turn: dict[str, Any],
    content: Any,
    tool_use_map: dict[str, bool],
) -> None:
    """Attach system-injected user content to current assistant turn."""
    blocks = _normalize_content(content)
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
            turns.append(
                {
                    "type": "result",
                    "subtype": msg.get("subtype", ""),
                    "uuid": msg.get("uuid"),
                    "timestamp": msg.get("timestamp"),
                }
            )
            continue

        if msg_type == "user":
            content = msg.get("content", "")
            if _is_system_injected_user_message(content):
                if current_turn and current_turn.get("type") == "assistant":
                    _attach_system_content_to_turn(current_turn, content, tool_use_map)
                elif not current_turn:
                    current_turn = {
                        "type": "system",
                        "content": _normalize_content(content),
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

        # Ignore other message types (stream_event/system/progress/etc)
        continue

    if current_turn:
        turns.append(current_turn)

    return turns


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
