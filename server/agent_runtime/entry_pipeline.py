"""Live persistence pipeline: SDK messages → event-log entries and streaming draft previews.

- Entries normalize, persist with allocated seq, then broadcast as ``log_entry``; SSE IDs equal seq.
- Drafts are server-memory state identified by ``message_id``. Deltas are transient broadcasts referencing
  message_id/block index, never logged. Completed authoritative entries clear drafts by exact matching message_id.
  Monotonic ``rev`` is included in the first reconnect snapshot; clients use it to filter duplicate deltas across
  subscription gaps by identity rather than content."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from server.agent_runtime.event_log import (
    ENTRY_SUBTYPE_AGENT_TURN_FAILURE,
    ENTRY_TYPE_ASSISTANT,
    REPLAYED_USER_ECHO_ENTRY_UUID_KEY,
    REPLAYED_USER_ECHO_KEY,
    EventLogStore,
    SdkMessageNormalizer,
    build_interrupt_entry,
    is_interrupt_entry,
)
from server.agent_runtime.failure_observation import failure_observation_json
from server.agent_runtime.turn_schema import normalize_block

logger = logging.getLogger(__name__)

# Bound retries for transient persistence failures, SQLite busy/connection glitches, abandoning entries only after exhaustion.
_APPEND_ATTEMPTS = 3
_APPEND_RETRY_BASE_S = 0.05

_T = TypeVar("_T")


def _coerce_index(value: Any) -> int | None:
    """Normalize stream event block index."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _safe_json_parse(value: str) -> Any | None:
    """Parse JSON string and return None when incomplete/invalid."""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


class DraftAccumulator:
    """Streaming previews accumulate content blocks by message_id in server memory, without persistence."""

    def __init__(self) -> None:
        self._message_id: str | None = None
        self._parent_tool_use_id: str | None = None
        self._blocks: dict[int, dict[str, Any]] = {}
        self._tool_input_json: dict[int, str] = {}
        # Monotonic across messages; snapshot rev is the reconnecting client’s delta threshold.
        self._rev = 0

    @property
    def message_id(self) -> str | None:
        return self._message_id

    @property
    def rev(self) -> int:
        return self._rev

    def apply_stream_event(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Process a stream_event and return its transient broadcast delta payload, or None."""
        event = message.get("event")
        if not isinstance(event, dict):
            return None
        event_type = event.get("type")

        if event_type == "message_start":
            raw_message = event.get("message")
            message_id = raw_message.get("id") if isinstance(raw_message, dict) else None
            self._reset_blocks()
            self._message_id = str(message_id) if message_id else None
            self._parent_tool_use_id = message.get("parent_tool_use_id") or None
            return None

        if self._message_id is None:
            # Orphan events without message_start lack identity; drafts require message_id,
            # so unassignable deltas never enter previews.
            return None

        if (message.get("parent_tool_use_id") or None) != self._parent_tool_use_id:
            # Parallel subagent streams interleave; previews have one slot using the latest
            # message_start. Filter other streams’ deltas by parent ownership
            # to avoid mixing drafts. Dropped streams’ complete content persists in authoritative entries.
            return None

        if event_type == "content_block_start":
            index = self._resolve_index(event)
            content_block = event.get("content_block")
            if not isinstance(content_block, dict):
                content_block = {"type": "text", "text": ""}
            block = normalize_block(content_block)
            self._blocks[index] = block
            return self._delta_payload("block_start", index, {"block": copy.deepcopy(block)})

        if event_type == "content_block_delta":
            delta = event.get("delta")
            if not isinstance(delta, dict):
                return None
            delta_type = delta.get("type")

            if delta_type == "text_delta":
                chunk = delta.get("text")
                if not isinstance(chunk, str) or chunk == "":
                    return None
                index = self._resolve_index(event)
                block = self._ensure_block(index, "text")
                block["type"] = "text"
                block["text"] = f"{block.get('text', '')}{chunk}"
                return self._delta_payload("text_delta", index, {"text": chunk})

            if delta_type == "thinking_delta":
                chunk = delta.get("thinking")
                if not isinstance(chunk, str) or chunk == "":
                    return None
                index = self._resolve_index(event)
                block = self._ensure_block(index, "thinking")
                block["type"] = "thinking"
                block["thinking"] = f"{block.get('thinking', '')}{chunk}"
                return self._delta_payload("thinking_delta", index, {"thinking": chunk})

            if delta_type == "input_json_delta":
                chunk = delta.get("partial_json")
                if not isinstance(chunk, str) or chunk == "":
                    return None
                index = self._resolve_index(event)
                block = self._ensure_block(index, "tool_use")
                block["type"] = "tool_use"
                if not isinstance(block.get("input"), dict):
                    block["input"] = {}
                updated_json = f"{self._tool_input_json.get(index, '')}{chunk}"
                self._tool_input_json[index] = updated_json
                parsed = _safe_json_parse(updated_json)
                if isinstance(parsed, dict):
                    block["input"] = parsed
                return self._delta_payload("input_json_delta", index, {"partial_json": chunk})

        return None

    def clear_for_message(self, message_id: Any) -> bool:
        """After authoritative entry persistence, clear its draft by exact message_id."""
        if not message_id or message_id != self._message_id:
            return False
        self._reset_blocks()
        self._message_id = None
        self._parent_tool_use_id = None
        return True

    def clear(self) -> None:
        """At turn completion, result or interruption, discard in-memory previews."""
        self._reset_blocks()
        self._message_id = None
        self._parent_tool_use_id = None

    def snapshot(self) -> dict[str, Any] | None:
        """First reconnect snapshot: current accumulated state, None without an active draft.

        ``tool_json`` carries accumulated raw partial JSON per tool_use block so reconnecting clients can append
        input_json_delta to that prefix; suffixes alone would never parse."""
        if self._message_id is None or not self._blocks:
            return None
        ordered = [copy.deepcopy(self._blocks[index]) for index in sorted(self._blocks)]
        return {
            "message_id": self._message_id,
            "parent_tool_use_id": self._parent_tool_use_id,
            "content": ordered,
            "rev": self._rev,
            "tool_json": dict(self._tool_input_json),
        }

    def _reset_blocks(self) -> None:
        self._blocks.clear()
        self._tool_input_json.clear()

    def _resolve_index(self, event: dict[str, Any]) -> int:
        index = _coerce_index(event.get("index"))
        if index is not None:
            return index
        if not self._blocks:
            return 0
        return max(self._blocks.keys())

    def _ensure_block(self, index: int, block_type: str) -> dict[str, Any]:
        block = self._blocks.get(index)
        if isinstance(block, dict):
            return block
        if block_type == "tool_use":
            block = {"type": "tool_use", "id": None, "name": "", "input": {}}
        elif block_type == "thinking":
            block = {"type": "thinking", "thinking": ""}
        else:
            block = {"type": "text", "text": ""}
        self._blocks[index] = block
        return block

    def _delta_payload(self, delta_type: str, index: int, extra: dict[str, Any]) -> dict[str, Any]:
        self._rev += 1
        return {
            "message_id": self._message_id,
            "parent_tool_use_id": self._parent_tool_use_id,
            "delta_type": delta_type,
            "block_index": index,
            "rev": self._rev,
            **extra,
        }


class SessionEntryPipeline:
    """One per session: consume inbox messages, persist entries and broadcast log_entry / log_delta."""

    def __init__(
        self,
        store: EventLogStore,
        *,
        session_id_provider: Callable[[], str | None],
        broadcast: Callable[[dict[str, Any]], None],
        project_name_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self._store = store
        self._session_id_provider = session_id_provider
        self._project_name_provider = project_name_provider or (lambda: None)
        self._broadcast = broadcast
        # One normalizer per session retains cross-message relations, Skill tool_use → injected messages
        # and AskUserQuestion registration → answer normalization, for session lifetime.
        self._normalizer = SdkMessageNormalizer()
        self.draft = DraftAccumulator()
        # Interrupt tail-check deduplication section: SDK echoes from inbox tasks and terminal-path
        # synthetic interrupts from actor-done callbacks can race; check/insert must be mutually exclusive.
        self._interrupt_lock = asyncio.Lock()

    async def handle_message(self, msg_dict: dict[str, Any]) -> None:
        """Persistence entry point. Failures log without interrupting sessions; replay reconstruction repairs timelines."""
        try:
            await self._handle(msg_dict)
        except Exception:
            logger.exception(
                "事件日志写入点处理失败 session_id=%s msg_type=%s",
                self._session_id_provider(),
                msg_dict.get("type"),
            )

    async def _handle(self, msg_dict: dict[str, Any]) -> None:
        session_id = self._session_id_provider()
        if not session_id:
            return
        msg_type = msg_dict.get("type")

        if msg_type == "stream_event":
            delta = self.draft.apply_stream_event(msg_dict)
            if delta is not None:
                self._broadcast({"type": "log_delta", "session_id": session_id, **delta})
            return

        if msg_type == "result":
            entries = self._normalizer.normalize(
                msg_dict,
                project_name=self._project_name_provider(),
                session_id=session_id,
            )
            await self._append_normalized(session_id, entries)
            # At turn end, discard drafts not replaced by authoritative entries, including interrupts/errors.
            self.draft.clear()
            # Interrupt results are timeline events; normalize from result when SDK echoes are absent,
            # deduplicating against logged echoes. Emit before log_turn_complete
            # or terminal entry-stream closure would lose interrupts for live subscribers.
            if str(msg_dict.get("session_status") or "") == "interrupted":
                appended = await self._append_interrupt_deduped(session_id, build_interrupt_entry())
                for entry in appended:
                    self._broadcast({"type": "log_entry", "session_id": session_id, "entry": entry})
            # Mark all turn entries persisted/broadcast in serial inbox order; entry streams
            # terminate from this marker so raw results cannot precede the last log_entry.
            self._broadcast({"type": "log_turn_complete", "session_id": session_id})
            return

        if msg_dict.get(REPLAYED_USER_ECHO_KEY):
            # Replay copies produce no entries, the normalizer returns empty, but uniquely carry transcript
            # entry identities; persist their mappings here before normal discard.
            await self._record_user_message_link(session_id, msg_dict)
            return

        entries = self._normalizer.normalize(
            msg_dict,
            project_name=self._project_name_provider(),
            session_id=session_id,
        )
        if msg_type == "assistant" and msg_dict.get("error") is not None:
            self.draft.clear_for_message(msg_dict.get("message_id"))
        if not entries:
            return
        await self._append_normalized(session_id, entries)

    async def _record_user_message_link(self, session_id: str, msg_dict: dict[str, Any]) -> None:
        """Persist server user-message ID ↔ SDK transcript entry uuid mapping.

        Skip if either ID is absent, from unassigned entry identity or SDK replay without uuid. This optional enhancement
        locates rewrite anchors; absence only prevents that message from being an anchor, without affecting the timeline.
        Persistence failures likewise only log."""
        entry_uuid = msg_dict.get(REPLAYED_USER_ECHO_ENTRY_UUID_KEY)
        sdk_entry_uuid = msg_dict.get("uuid")
        if not entry_uuid or not sdk_entry_uuid:
            return
        try:
            await self._store.record_user_message_link(session_id, str(entry_uuid), str(sdk_entry_uuid))
        except Exception:
            logger.exception(
                "用户消息身份映射落库失败 session_id=%s user_entry_uuid=%s",
                session_id,
                entry_uuid,
            )

    async def _append_normalized(self, session_id: str, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        for entry in entries:
            if entry.get("type") == "system" and entry.get("subtype") == ENTRY_SUBTYPE_AGENT_TURN_FAILURE:
                failure = entry.get("failure")
                if isinstance(failure, dict):
                    logger.warning("agent turn failure failure_observation=%s", failure_observation_json(failure))
        if len(entries) == 1 and is_interrupt_entry(entries[0]):
            appended = await self._append_interrupt_deduped(session_id, entries[0])
        else:
            appended = await self._append_with_retry(session_id, entries)
        for entry in appended:
            self._broadcast({"type": "log_entry", "session_id": session_id, "entry": entry})
        for entry in appended:
            if entry.get("type") == ENTRY_TYPE_ASSISTANT and self.draft.clear_for_message(entry.get("message_id")):
                break

    async def append_user_entry(self, entry: dict[str, Any], *, client_key: str | None = None) -> None:
        """On Agent acceptance of queued messages, persist/broadcast user entries after previously produced output.

        Failures log without interrupting sessions; replay reconstruction repairs timelines."""
        session_id = self._session_id_provider()
        if not session_id:
            return
        try:
            authoritative, _created = await self._retrying(
                lambda: self._store.append_user_entry(session_id, entry, client_key=client_key)
            )
        except Exception:
            logger.exception("用户条目写入事件日志失败 session_id=%s entry_uuid=%s", session_id, entry.get("uuid"))
            return
        self._broadcast({"type": "log_entry", "session_id": session_id, "entry": authoritative})

    async def append_interrupt(self) -> list[dict[str, Any]]:
        """Direct interrupt persistence for terminal paths when SDK echoes no longer arrive via inbox.

        Share tail-check deduplication with SDK echoes so adjacent interrupt races produce only one entry."""
        session_id = self._session_id_provider()
        if not session_id:
            return []
        appended = await self._append_interrupt_deduped(session_id, build_interrupt_entry())
        for entry in appended:
            self._broadcast({"type": "log_entry", "session_id": session_id, "entry": entry})
        return appended

    async def _append_interrupt_deduped(self, session_id: str, entry: dict[str, Any]) -> list[dict[str, Any]]:
        async with self._interrupt_lock:
            tail = await self._store.last_entry(session_id)
            if tail is not None and is_interrupt_entry(tail):
                return []
            return await self._append_with_retry(session_id, [entry])

    async def _append_with_retry(self, session_id: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return await self._retrying(lambda: self._store.append(session_id, entries))

    @staticmethod
    async def _retrying(operation: Callable[[], Awaitable[_T]]) -> _T:
        """Bounded persistence retries prevent transient database failures from leaving permanent append-only log gaps."""
        for attempt in range(_APPEND_ATTEMPTS):
            try:
                return await operation()
            except Exception:
                if attempt == _APPEND_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(_APPEND_RETRY_BASE_S * (attempt + 1))
        raise RuntimeError("unreachable")  # pragma: no cover
