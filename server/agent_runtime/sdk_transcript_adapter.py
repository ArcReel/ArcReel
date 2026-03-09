"""SDK-based transcript adapter replacing manual JSONL parsing."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from claude_agent_sdk import get_session_messages
    try:
        # Public get_session_messages() drops the transcript-level timestamp, but
        # optimistic-turn dedup needs stable per-message ordering to distinguish
        # repeated prompts across rounds. Until the SDK exposes timestamps via a
        # public API, we backfill them from the raw JSONL transcript here.
        from claude_agent_sdk._internal.sessions import _read_session_file
    except ImportError:
        _read_session_file = None  # type: ignore[assignment]
    SDK_AVAILABLE = True
except ImportError:
    get_session_messages = None  # type: ignore[assignment]
    _read_session_file = None  # type: ignore[assignment]
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
            logger.warning("Failed to read SDK session %s", sdk_session_id, exc_info=True)
            return []
        timestamp_by_uuid = self._load_timestamps(sdk_session_id)
        return [self._adapt(msg, timestamp_by_uuid) for msg in sdk_messages]

    def _adapt(self, msg: Any, timestamp_by_uuid: Optional[dict[str, str]] = None) -> dict[str, Any]:
        """Convert SDK SessionMessage to internal dict format."""
        message_data = getattr(msg, "message", {}) or {}
        if isinstance(message_data, dict):
            content = message_data.get("content", "")
        else:
            content = ""

        uuid = getattr(msg, "uuid", None)
        timestamp = getattr(msg, "timestamp", None)
        if timestamp is None and isinstance(uuid, str) and timestamp_by_uuid:
            timestamp = timestamp_by_uuid.get(uuid)

        result: dict[str, Any] = {
            "type": getattr(msg, "type", ""),
            "content": content,
            "uuid": uuid,
            "timestamp": timestamp,
        }

        parent_tool_use_id = getattr(msg, "parent_tool_use_id", None)
        if parent_tool_use_id:
            result["parent_tool_use_id"] = parent_tool_use_id

        return result

    def _load_timestamps(self, sdk_session_id: str) -> dict[str, str]:
        """Read raw JSONL transcript and build a uuid -> timestamp index.

        This is a compatibility shim around the current SDK surface: the public
        SessionMessage model omits transcript timestamps, so identical user
        prompts across rounds cannot be ordered reliably without re-reading the
        raw transcript. Prefer replacing this with a public SDK API once one is
        available.
        """
        if _read_session_file is None:
            return {}
        try:
            content = _read_session_file(sdk_session_id, None)
        except Exception:
            logger.warning(
                "Failed to read raw SDK transcript %s for timestamps",
                sdk_session_id,
                exc_info=True,
            )
            return {}
        if not content:
            return {}

        timestamps: dict[str, str] = {}
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(entry, dict):
                continue
            uuid = entry.get("uuid")
            timestamp = entry.get("timestamp")
            if isinstance(uuid, str) and uuid and isinstance(timestamp, str) and timestamp.strip():
                timestamps[uuid] = timestamp.strip()
        return timestamps

    def exists(self, sdk_session_id: Optional[str]) -> bool:
        """Check if SDK session has any messages."""
        if not sdk_session_id or not SDK_AVAILABLE or get_session_messages is None:
            return False
        try:
            messages = get_session_messages(sdk_session_id, limit=1)
            return len(messages) > 0
        except Exception:
            logger.warning("Failed to check existence of SDK session %s", sdk_session_id, exc_info=True)
            return False
