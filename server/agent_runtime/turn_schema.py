"""
Shared Turn normalization contract.

All code paths that produce Turn payloads (turn_grouper, stream_projector,
service) MUST go through these functions to guarantee a consistent shape
for the frontend.

Turn Contract:
    Turn = {
        "type": "user" | "assistant" | "system" | "result",
        "content": list[ContentBlock],   # always list, never string
        "uuid": str | None,
        "timestamp": str | None,
    }

    ContentBlock = {
        "type": str,                     # always present
        "text": str,                     # Optional
        "thinking": str,                 # Optional
        "id": str | None,                # Optional
        "name": str,                     # Optional
        "input": dict,                   # Optional, always dict when present
        "result": str,                   # Optional
        "is_error": bool,                # Optional
        "skill_content": str,            # Optional
        "tool_use_id": str,              # Optional
        "content": str,                  # Optional
    }
"""

from __future__ import annotations

import copy
from typing import Any


def infer_block_type(block: dict[str, Any]) -> str:
    """Infer content block type when SDK omits explicit ``type``.

    Ported from turn_grouper._infer_block_type() with added ``thinking``
    detection for stream_projector compatibility.
    """
    explicit_type = block.get("type")
    if isinstance(explicit_type, str) and explicit_type:
        return explicit_type

    if block.get("tool_use_id") and ("content" in block or "is_error" in block):
        return "tool_result"

    if block.get("id") and block.get("name") and "input" in block:
        return "tool_use"

    if "thinking" in block:
        return "thinking"

    if "text" in block:
        return "text"

    return ""


def normalize_block(block: Any) -> dict[str, Any]:
    """Normalize a single content block.

    Combines the logic from:
    - turn_grouper._normalize_block()  (type inference)
    - stream_projector._normalize_block() (default values)
    """
    if not isinstance(block, dict):
        if isinstance(block, str):
            return {"type": "text", "text": block}
        return {"type": "text", "text": str(block)}

    normalized: dict[str, Any] = copy.deepcopy(block)

    # 1. Infer type if missing
    block_type = infer_block_type(normalized)
    if block_type:
        normalized["type"] = block_type
    elif "type" not in normalized:
        normalized["type"] = "text"

    # 2. Ensure default values based on type
    block_type = normalized["type"]
    if block_type == "text":
        normalized.setdefault("text", "")
    elif block_type == "thinking":
        normalized.setdefault("thinking", "")
    elif block_type == "tool_use":
        if not isinstance(normalized.get("input"), dict):
            normalized["input"] = {}

    return normalized


def normalize_content(content: Any) -> list[dict[str, Any]]:
    """Normalize message content to always be ``list[dict]``.

    Ported from turn_grouper._normalize_content().
    """
    if isinstance(content, str):
        if not content.strip():
            return []
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        normalized_blocks: list[dict[str, Any]] = []
        for block in content:
            normalized = normalize_block(block)
            if isinstance(normalized, dict):
                normalized_blocks.append(normalized)
        return normalized_blocks
    return []


def normalize_turn(turn: dict[str, Any]) -> dict[str, Any]:
    """Ensure a Turn satisfies the Turn Contract.

    - ``content`` is always ``list[dict]``
    - every block has a ``type`` field
    - ``tool_use.input`` is always ``dict``
    - ``uuid`` and ``timestamp`` keys always exist (may be ``None``)
    """
    result = copy.deepcopy(turn)

    # Ensure content is list[dict]
    result["content"] = normalize_content(result.get("content", []))

    # Ensure uuid and timestamp keys exist
    result.setdefault("uuid", None)
    result.setdefault("timestamp", None)

    return result


def normalize_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Batch-normalize a list of turns. Used as final gate before API output."""
    return [normalize_turn(turn) for turn in turns]
