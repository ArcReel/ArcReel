"""Shared message utility functions for agent_runtime."""

from typing import Any, Optional


def _extract_text_from_block(block: Any) -> Optional[str]:
    """Return stripped text from a content block dict, or None."""
    if not isinstance(block, dict):
        return None
    if block.get("type") not in {"text", None}:
        return None
    text = block.get("text")
    if not isinstance(text, str):
        return None
    text = text.strip()
    return text or None


def extract_plain_user_content(message: dict[str, Any]) -> Optional[str]:
    """Extract plain text from a user message payload.

    Used for echo dedup in both service and session_manager layers.
    Supports plain string content, single text block, and multi-block
    content (e.g. image + text).
    """
    if message.get("type") != "user":
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, list):
        return None
    for block in content:
        text = _extract_text_from_block(block)
        if text:
            return text
    return None
