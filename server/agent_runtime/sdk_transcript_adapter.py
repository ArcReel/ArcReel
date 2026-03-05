"""SDK-based transcript adapter replacing manual JSONL parsing."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from claude_agent_sdk import get_session_messages
    SDK_AVAILABLE = True
except ImportError:
    get_session_messages = None  # type: ignore[assignment]
    SDK_AVAILABLE = False


class SdkTranscriptAdapter:
    """Read conversation history via SDK get_session_messages().

    Replaces TranscriptReader's manual JSONL parsing with SDK's
    parentUuid chain reconstruction, which correctly handles:
    - Compacted sessions
    - Branch/sidechain filtering
    - Mainline conversation chain
    """

    def read_raw_messages(self, sdk_session_id: Optional[str]) -> list[dict[str, Any]]:
        """Read raw messages from SDK session transcript."""
        if not sdk_session_id or not SDK_AVAILABLE or get_session_messages is None:
            return []
        try:
            sdk_messages = get_session_messages(sdk_session_id)
        except Exception:
            logger.debug("Failed to read SDK session %s", sdk_session_id, exc_info=True)
            return []
        return [self._adapt(msg) for msg in sdk_messages]

    def _adapt(self, msg: Any) -> dict[str, Any]:
        """Convert SDK SessionMessage to internal dict format."""
        message_data = getattr(msg, "message", {}) or {}
        if isinstance(message_data, dict):
            content = message_data.get("content", "")
        else:
            content = ""

        result: dict[str, Any] = {
            "type": getattr(msg, "type", ""),
            "content": content,
            "uuid": getattr(msg, "uuid", None),
            "timestamp": getattr(msg, "timestamp", None),
        }

        parent_tool_use_id = getattr(msg, "parent_tool_use_id", None)
        if parent_tool_use_id:
            result["parent_tool_use_id"] = parent_tool_use_id

        return result

    def exists(self, sdk_session_id: Optional[str]) -> bool:
        """Check if SDK session has any messages."""
        if not sdk_session_id or not SDK_AVAILABLE or get_session_messages is None:
            return False
        try:
            messages = get_session_messages(sdk_session_id, limit=1)
            return len(messages) > 0
        except Exception:
            return False
