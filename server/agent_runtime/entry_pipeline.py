"""Live 写入点管道：SDK 消息流 → 事件日志条目 + 流式预览态（draft）。

- 条目：normalize 后落库分配 seq，再以 ``log_entry`` 广播（SSE 事件 id 即 seq）。
- draft：服务端内存态，身份为消息 ``message_id``；delta 为瞬时广播事件
  （引用 message_id + block index），不入日志；消息完成时被同 message_id
  的权威条目精确清除。``rev`` 单调递增，重连首帧快照携带当前 rev，客户端
  以此过滤订阅间隙内重复投递的 delta——身份比对，不做内容比对。
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from typing import Any

from server.agent_runtime.event_log import (
    ENTRY_TYPE_ASSISTANT,
    EventLogStore,
    normalize_sdk_message_to_entries,
)
from server.agent_runtime.stream_projector import _coerce_index, _safe_json_parse
from server.agent_runtime.turn_schema import normalize_block

logger = logging.getLogger(__name__)


class DraftAccumulator:
    """流式预览态：按 message_id 累积 content block，服务端内存态、不落盘。"""

    def __init__(self) -> None:
        self._message_id: str | None = None
        self._parent_tool_use_id: str | None = None
        self._blocks: dict[int, dict[str, Any]] = {}
        self._tool_input_json: dict[int, str] = {}
        # 跨消息单调递增：快照携带的 rev 是重连客户端的 delta 过滤门槛。
        self._rev = 0

    @property
    def message_id(self) -> str | None:
        return self._message_id

    @property
    def rev(self) -> int:
        return self._rev

    def apply_stream_event(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """处理一条 stream_event 消息，返回要广播的瞬时 delta 载荷（或 None）。"""
        event = message.get("event")
        if not isinstance(event, dict):
            return None
        event_type = event.get("type")

        if event_type == "message_start":
            raw_message = event.get("message")
            message_id = raw_message.get("id") if isinstance(raw_message, dict) else None
            self._reset_blocks()
            self._message_id = str(message_id) if message_id else None
            self._parent_tool_use_id = message.get("parent_tool_use_id")
            return None

        if self._message_id is None:
            # 无身份的孤儿事件（message_start 缺失）：draft 契约以 message_id
            # 为身份，无法归属的增量不进入预览态。
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
        """权威条目落库后按同 message_id 精确清除对应 draft。"""
        if not message_id or message_id != self._message_id:
            return False
        self._reset_blocks()
        self._message_id = None
        self._parent_tool_use_id = None
        return True

    def clear(self) -> None:
        """轮次终结（result / 中断）：预览态随内存丢弃。"""
        self._reset_blocks()
        self._message_id = None
        self._parent_tool_use_id = None

    def snapshot(self) -> dict[str, Any] | None:
        """重连首帧快照：当前累积态（无活跃 draft 时为 None）。"""
        if self._message_id is None or not self._blocks:
            return None
        ordered = [copy.deepcopy(self._blocks[index]) for index in sorted(self._blocks)]
        return {
            "message_id": self._message_id,
            "parent_tool_use_id": self._parent_tool_use_id,
            "content": ordered,
            "rev": self._rev,
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
    """每会话一个：消费 inbox 消息，产出日志条目写入与 log_entry / log_delta 广播。"""

    def __init__(
        self,
        store: EventLogStore,
        *,
        session_id_provider: Callable[[], str | None],
        broadcast: Callable[[dict[str, Any]], None],
    ) -> None:
        self._store = store
        self._session_id_provider = session_id_provider
        self._broadcast = broadcast
        self.draft = DraftAccumulator()

    async def handle_message(self, msg_dict: dict[str, Any]) -> None:
        """写入点入口。失败只记日志不打断会话——时间线的修复手段是重放重建。"""
        try:
            await self._handle(msg_dict)
        except Exception:
            logger.exception(
                "事件日志写入点处理失败 session_id=%s msg_type=%s",
                self._session_id_provider(),
                msg_dict.get("type") if isinstance(msg_dict, dict) else type(msg_dict),
            )

    async def _handle(self, msg_dict: dict[str, Any]) -> None:
        if not isinstance(msg_dict, dict):
            return
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
            # 轮次终结：未被权威条目替换的 draft（中断/错误）随内存丢弃。
            self.draft.clear()
            return

        entries = normalize_sdk_message_to_entries(msg_dict)
        if not entries:
            return
        appended = await self._store.append(session_id, entries)
        for entry in appended:
            self._broadcast({"type": "log_entry", "session_id": session_id, "entry": entry})
        for entry in appended:
            if entry.get("type") == ENTRY_TYPE_ASSISTANT and self.draft.clear_for_message(entry.get("message_id")):
                break
