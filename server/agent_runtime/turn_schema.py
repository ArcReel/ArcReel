"""
Shared content-block normalization contract.

All code paths that persist or broadcast content blocks (event log write
point, entry pipeline draft) MUST go through these functions to guarantee a
consistent shape for the frontend.

    ContentBlock = {
        "type": str,                     # always present
        "text": str,                     # Optional
        "thinking": str,                 # Optional
        "id": str | None,                # Optional
        "name": str,                     # Optional
        "input": dict,                   # Optional, always dict when present
        "result": str,                   # Optional
        "is_error": bool,                # Optional
        "tool_use_id": str,              # Optional
        "content": str,                  # Optional
    }
"""

from __future__ import annotations

import copy
from typing import Any


def _stringify_content(content: Any) -> str:
    """Ensure tool_result content is always a string.

    The Claude SDK may send tool_result content as a list of content blocks
    (e.g. ``[{"type": "text", "text": "..."}]``).  The frontend expects a
    plain string, so we flatten arrays here.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def infer_block_type(block: dict[str, Any]) -> str:
    """Infer content block type when SDK omits explicit ``type``."""
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
    """Normalize a single content block (type inference + default values)."""
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
    elif block_type == "tool_result":
        normalized["content"] = _stringify_content(normalized.get("content", ""))
    elif block_type == "image":
        pass  # image blocks pass through as-is (source field preserved by deepcopy)

    return normalized


def normalize_content(content: Any) -> list[dict[str, Any]]:
    """Normalize message content to always be ``list[dict]``."""
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
