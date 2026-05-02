"""SDK-based transcript adapter using public SessionStore helpers.

Reads conversation history via ``get_session_messages_from_store`` when a
SessionStore is wired in, or falls back to ``get_session_messages``
(filesystem) when ``ARCREEL_SDK_SESSION_STORE=off`` is set.

The store path eliminates the previous dependency on the private
``_internal._read_session_file`` symbol. SDK 0.1.71's reconstructed
``SessionMessage`` does not carry a ``timestamp`` field, so the adapter
backfills timestamps by re-reading payloads via ``store.load(key)`` and
joining on ``uuid`` — keeps optimistic-turn dedup stable across rounds
without reaching into SDK internals.
"""

from __future__ import annotations

import asyncio
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
        return await self._read_via_legacy(sdk_session_id)

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

        # SDK 0.1.71 SessionMessage has no timestamp field — backfill from the
        # store payload we wrote in append() (preserves SDK's payload.timestamp
        # verbatim). This keeps optimistic-turn dedup stable across rounds.
        timestamp_by_uuid = await self._load_timestamps_from_store(sdk_session_id, project_cwd)
        return [self._adapt(msg, timestamp_by_uuid) for msg in (messages or [])]

    async def _load_timestamps_from_store(
        self,
        sdk_session_id: str,
        project_cwd: Path | str | None,
    ) -> dict[str, str]:
        """Build uuid -> timestamp index by reading store payloads.

        SDK's get_session_messages_from_store reconstructs SessionMessage objects
        that lack the per-entry timestamp field. We re-fetch raw payloads via
        store.load() and join on uuid so downstream consumers (turn_grouper)
        keep getting stable timestamps without touching SDK private APIs.
        """
        if project_cwd is None:
            return {}
        try:
            from lib.agent_session_store import make_project_key

            key = {
                "project_key": make_project_key(project_cwd),
                "session_id": sdk_session_id,
            }
            payloads = await self._store.load(key)
        except Exception:
            logger.warning(
                "Failed to load timestamps for session %s",
                sdk_session_id,
                exc_info=True,
            )
            return {}
        if not payloads:
            return {}
        ts_map: dict[str, str] = {}
        for entry in payloads:
            if not isinstance(entry, dict):
                continue
            uuid = entry.get("uuid")
            ts = entry.get("timestamp")
            if isinstance(uuid, str) and uuid and isinstance(ts, str) and ts.strip():
                ts_map[uuid] = ts.strip()
        return ts_map

    async def _read_via_legacy(self, sdk_session_id: str) -> list[dict[str, Any]]:
        """Filesystem fallback for ARCREEL_SDK_SESSION_STORE=off."""
        if get_session_messages is None:
            return []
        try:
            # SDK reader walks the JSONL transcript synchronously; offload so
            # SSE streaming and other coroutines aren't blocked while we wait
            # on disk I/O for large histories.
            sdk_messages = await asyncio.to_thread(get_session_messages, sdk_session_id)
        except Exception:
            logger.warning(
                "Failed to read SDK session %s",
                sdk_session_id,
                exc_info=True,
            )
            return []
        # Legacy SDK messages may carry timestamps directly; no map needed.
        return [self._adapt(m) for m in sdk_messages]

    @staticmethod
    def _coerce_cwd(project_cwd: Path | str | None) -> str | None:
        if project_cwd is None:
            return None
        return str(project_cwd)

    def _adapt(
        self,
        msg: Any,
        timestamp_by_uuid: dict[str, str] | None = None,
    ) -> dict[str, Any]:
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
