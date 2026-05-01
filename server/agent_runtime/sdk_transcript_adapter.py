"""SDK-based transcript adapter using public SessionStore helpers.

Reads conversation history via ``get_session_messages_from_store`` when a
SessionStore is wired in, or falls back to ``get_session_messages``
(filesystem) when ``ARCREEL_SDK_SESSION_STORE=off`` is set.

The store path eliminates the previous dependency on the private
``_internal._read_session_file`` symbol — SessionMessage objects served by
the store helper carry their original ``payload.timestamp`` already
(persisted verbatim by ``DbSessionStore`` in Task 4), so no transcript
backfill is required to keep optimistic-turn dedup ordering stable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from claude_agent_sdk import (
        get_session_messages,
        get_session_messages_from_store,
    )

    SDK_AVAILABLE = True
except ImportError:
    get_session_messages = None  # type: ignore[assignment]
    get_session_messages_from_store = None  # type: ignore[assignment]
    SDK_AVAILABLE = False


class SdkTranscriptAdapter:
    """Read SDK conversation transcripts.

    Constructed with an optional store. When the store is present, reads go
    through the SDK's SessionStore helpers; otherwise they fall back to the
    SDK's filesystem reader (``get_session_messages``) so the rollback path
    (``ARCREEL_SDK_SESSION_STORE=off``) still works.

    ``project_cwd`` is supplied per call because a single AssistantService
    instance serves many projects.
    """

    def __init__(self, store: Any = None) -> None:
        self._store = store

    async def read_raw_messages(
        self,
        sdk_session_id: str | None,
        project_cwd: Path | str | None = None,
    ) -> list[dict[str, Any]]:
        """Read raw messages from the SDK transcript."""
        if not sdk_session_id or not SDK_AVAILABLE:
            return []
        if self._store is not None and get_session_messages_from_store is not None:
            return await self._read_via_store(sdk_session_id, project_cwd)
        return self._read_via_legacy(sdk_session_id)

    async def exists(
        self,
        sdk_session_id: str | None,
        project_cwd: Path | str | None = None,
    ) -> bool:
        """True if the session has at least one entry."""
        if not sdk_session_id or not SDK_AVAILABLE:
            return False
        if self._store is not None and get_session_messages_from_store is not None:
            try:
                messages = await get_session_messages_from_store(
                    self._store,
                    sdk_session_id,
                    directory=self._coerce_cwd(project_cwd),
                    limit=1,
                )
                return len(messages) > 0
            except Exception:
                logger.warning(
                    "Failed to check existence (store) of SDK session %s",
                    sdk_session_id,
                    exc_info=True,
                )
                return False
        # legacy path
        if get_session_messages is None:
            return False
        try:
            messages = get_session_messages(sdk_session_id, limit=1)
            return len(messages) > 0
        except Exception:
            logger.warning(
                "Failed to check existence of SDK session %s",
                sdk_session_id,
                exc_info=True,
            )
            return False

    async def _read_via_store(
        self,
        sdk_session_id: str,
        project_cwd: Path | str | None,
    ) -> list[dict[str, Any]]:
        try:
            messages = await get_session_messages_from_store(
                self._store,
                sdk_session_id,
                directory=self._coerce_cwd(project_cwd),
            )
        except Exception:
            logger.warning(
                "Failed to read SDK session %s via store",
                sdk_session_id,
                exc_info=True,
            )
            return []
        return [self._adapt(msg) for msg in (messages or [])]

    def _read_via_legacy(self, sdk_session_id: str) -> list[dict[str, Any]]:
        """Filesystem fallback for ARCREEL_SDK_SESSION_STORE=off."""
        if get_session_messages is None:
            return []
        try:
            sdk_messages = get_session_messages(sdk_session_id)
        except Exception:
            logger.warning(
                "Failed to read SDK session %s",
                sdk_session_id,
                exc_info=True,
            )
            return []
        return [self._adapt(m) for m in sdk_messages]

    @staticmethod
    def _coerce_cwd(project_cwd: Path | str | None) -> str | None:
        if project_cwd is None:
            return None
        return str(project_cwd)

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
