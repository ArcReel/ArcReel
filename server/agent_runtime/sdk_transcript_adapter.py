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

from claude_agent_sdk import (
    get_session_messages,
    get_session_messages_from_store,
    get_subagent_messages_from_store,
    list_subagents_from_store,
)

SDK_AVAILABLE = True


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
        # toolUseResult（CLI 的结构化工具结果，如 AskUserQuestion 的答案）
        # 同样只存在于 payload，一并按 uuid 回填。
        payload_by_uuid = await self._load_payload_index_from_store(sdk_session_id, project_cwd)
        return [self._adapt(msg, payload_by_uuid) for msg in (messages or [])]

    async def _load_raw_payloads(
        self,
        sdk_session_id: str,
        project_cwd: Path | str | None,
        subpath: str = "",
    ) -> list[dict[str, Any]]:
        """Load raw transcript payload dicts via store.load() (empty on failure)."""
        if project_cwd is None:
            logger.debug(
                "Skip raw payload load for session %s subpath=%s: project_cwd is None",
                sdk_session_id,
                subpath or "<main>",
            )
            return []
        try:
            from lib.agent_session_store import make_project_key

            key: dict[str, Any] = {
                "project_key": make_project_key(project_cwd),
                "session_id": sdk_session_id,
            }
            if subpath:
                key["subpath"] = subpath
            payloads = await self._store.load(key)
        except Exception:
            logger.warning(
                "Failed to load raw payloads for session %s subpath=%s",
                sdk_session_id,
                subpath or "<main>",
                exc_info=True,
            )
            return []
        return [entry for entry in payloads or [] if isinstance(entry, dict)]

    async def _load_payload_index_from_store(
        self,
        sdk_session_id: str,
        project_cwd: Path | str | None,
        subpath: str = "",
    ) -> dict[str, dict[str, Any]]:
        """Build uuid -> raw payload index by reading store payloads.

        SDK's get_session_messages_from_store / get_subagent_messages_from_store
        reconstruct SessionMessage objects that lack the per-entry timestamp /
        toolUseResult fields. We re-fetch raw payloads via store.load() and join
        on uuid so downstream consumers keep getting stable timestamps and
        structured tool results (e.g. AskUserQuestion 的 answers) without
        touching SDK private APIs. Works for both the main transcript
        (subpath="") and subagent subpaths.
        """
        payloads = await self._load_raw_payloads(sdk_session_id, project_cwd, subpath)
        index: dict[str, dict[str, Any]] = {}
        for entry in payloads:
            uuid = entry.get("uuid")
            if isinstance(uuid, str) and uuid:
                index[uuid] = entry
        return index

    async def read_subagent_timelines(
        self,
        sdk_session_id: str | None,
        project_cwd: Path | str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """读取 subagent 子时间线，按主线 Task tool_use id 锚定归组。

        锚定依据主 transcript 原始载荷中 Task tool_result 的
        ``toolUseResult.agentId`` 与同载荷 tool_result 块的 ``tool_use_id``。
        文件系统回退路径（``ARCREEL_SDK_SESSION_STORE=off``）的公开读取接口
        不携带该元数据，无法锚定，降级为不合并（子时间线仍在 transcript 中，
        不丢数据）。
        """
        if not sdk_session_id or not SDK_AVAILABLE:
            return {}
        if self._store is None:
            logger.info(
                "Subagent timeline merge unavailable without session store (session %s)",
                sdk_session_id,
            )
            return {}
        anchors = await self._load_agent_anchors(sdk_session_id, project_cwd)
        if not anchors:
            return {}
        try:
            agent_ids = await list_subagents_from_store(
                self._store,
                sdk_session_id,
                directory=self._coerce_cwd(project_cwd),
            )
        except Exception:
            logger.warning(
                "Failed to list subagents for session %s",
                sdk_session_id,
                exc_info=True,
            )
            return {}
        # 各 subagent 的 store 读取相互独立，并发拉取避免 N 个 subagent 时
        # 冷读路径按 N 次网络/磁盘往返的延迟串行叠加。
        results = await asyncio.gather(
            *(
                self._read_one_subagent_timeline(sdk_session_id, project_cwd, agent_id, anchors.get(agent_id))
                for agent_id in agent_ids
            )
        )
        return {tool_use_id: messages for tool_use_id, messages in results if tool_use_id and messages}

    async def _read_one_subagent_timeline(
        self,
        sdk_session_id: str,
        project_cwd: Path | str | None,
        agent_id: str,
        tool_use_id: str | None,
    ) -> tuple[str | None, list[dict[str, Any]] | None]:
        """读取单个 subagent 的子时间线；无锚点或读取失败时返回 (None, None)。"""
        if not tool_use_id:
            logger.debug("Subagent %s has no Task tool_use anchor; skipped", agent_id)
            return None, None
        try:
            messages = await get_subagent_messages_from_store(
                self._store,
                sdk_session_id,
                agent_id,
                directory=self._coerce_cwd(project_cwd),
            )
        except Exception:
            logger.warning(
                "Failed to read subagent %s messages for session %s",
                agent_id,
                sdk_session_id,
                exc_info=True,
            )
            return None, None
        if not messages:
            return None, None
        payload_by_uuid = await self._load_payload_index_from_store(
            sdk_session_id,
            project_cwd,
            subpath=f"subagents/agent-{agent_id}",
        )
        return tool_use_id, [self._adapt(msg, payload_by_uuid) for msg in messages]

    async def _load_agent_anchors(
        self,
        sdk_session_id: str,
        project_cwd: Path | str | None,
    ) -> dict[str, str]:
        """agent_id → 主线 Task tool_use id 锚定映射。"""
        payloads = await self._load_raw_payloads(sdk_session_id, project_cwd)
        anchors: dict[str, str] = {}
        for entry in payloads:
            tool_use_result = entry.get("toolUseResult")
            if not isinstance(tool_use_result, dict):
                continue
            agent_id = tool_use_result.get("agentId")
            if not isinstance(agent_id, str) or not agent_id:
                continue
            tool_use_id = _first_tool_result_use_id(entry)
            if tool_use_id:
                anchors.setdefault(agent_id, tool_use_id)
        return anchors

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
        payload_by_uuid: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Convert SDK SessionMessage to internal dict format."""
        message_data = getattr(msg, "message", {}) or {}
        if isinstance(message_data, dict):
            content = message_data.get("content", "")
        else:
            content = ""

        uuid = getattr(msg, "uuid", None)
        payload = payload_by_uuid.get(uuid) if payload_by_uuid and isinstance(uuid, str) else None

        timestamp = getattr(msg, "timestamp", None)
        if timestamp is None and payload is not None:
            payload_ts = payload.get("timestamp")
            if isinstance(payload_ts, str) and payload_ts.strip():
                timestamp = payload_ts.strip()

        result: dict[str, Any] = {
            "type": getattr(msg, "type", ""),
            "content": content,
            "uuid": uuid,
            "timestamp": timestamp,
        }

        tool_use_result = getattr(msg, "tool_use_result", None)
        if tool_use_result is None and payload is not None:
            tool_use_result = payload.get("toolUseResult")
        if isinstance(tool_use_result, dict):
            result["tool_use_result"] = tool_use_result

        parent_tool_use_id = getattr(msg, "parent_tool_use_id", None)
        if parent_tool_use_id:
            result["parent_tool_use_id"] = parent_tool_use_id

        return result


def _first_tool_result_use_id(entry: dict[str, Any]) -> str | None:
    """Extract the first tool_result block's tool_use_id from a raw payload."""
    message = entry.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            tool_use_id = block.get("tool_use_id")
            if isinstance(tool_use_id, str) and tool_use_id:
                return tool_use_id
    return None
