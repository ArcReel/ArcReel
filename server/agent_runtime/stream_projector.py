"""
Shared projector for assistant snapshots and live streaming updates.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Optional

from server.agent_runtime.turn_grouper import build_turn_patch, group_messages_into_turns
from server.agent_runtime.turn_schema import (
    normalize_block as _shared_normalize_block,
    normalize_turn,
)

_GROUPABLE_TYPES = {"user", "assistant", "result"}


def _coerce_index(value: Any) -> Optional[int]:
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


def _safe_json_parse(value: str) -> Optional[Any]:
    """Parse JSON string and return None when incomplete/invalid."""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


class DraftAssistantProjector:
    """Builds an in-flight assistant turn from StreamEvent payloads."""

    def __init__(self):
        self._blocks_by_index: dict[int, dict[str, Any]] = {}
        self._tool_input_json: dict[int, str] = {}
        self._session_id: Optional[str] = None
        self._parent_tool_use_id: Optional[str] = None

    def clear(self) -> None:
        self._blocks_by_index.clear()
        self._tool_input_json.clear()
        self._session_id = None
        self._parent_tool_use_id = None

    def _default_index(self) -> int:
        if not self._blocks_by_index:
            return 0
        return max(self._blocks_by_index.keys())

    def _ensure_block(self, index: int, block_type: str) -> dict[str, Any]:
        block = self._blocks_by_index.get(index)
        if isinstance(block, dict):
            return block

        if block_type == "tool_use":
            block = {"type": "tool_use", "id": None, "name": "", "input": {}}
        elif block_type == "thinking":
            block = {"type": "thinking", "thinking": ""}
        else:
            block = {"type": "text", "text": ""}

        self._blocks_by_index[index] = block
        return block

    def _resolve_index(self, event: dict[str, Any]) -> int:
        index = _coerce_index(event.get("index"))
        if index is not None:
            return index
        return self._default_index()

    def apply_stream_event(self, stream_message: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Apply one stream_event message and return a delta payload when applicable."""
        event = stream_message.get("event")
        if not isinstance(event, dict):
            return None

        self._session_id = stream_message.get("session_id") or self._session_id
        self._parent_tool_use_id = (
            stream_message.get("parent_tool_use_id")
            or self._parent_tool_use_id
        )

        event_type = event.get("type")
        if event_type == "message_start":
            self.clear()
            self._session_id = stream_message.get("session_id")
            self._parent_tool_use_id = stream_message.get("parent_tool_use_id")
            return None

        if event_type == "content_block_start":
            index = self._resolve_index(event)
            content_block = event.get("content_block")
            if not isinstance(content_block, dict):
                content_block = {"type": "text", "text": ""}
            self._blocks_by_index[index] = _shared_normalize_block(content_block)
            return None

        if event_type == "content_block_delta":
            index = self._resolve_index(event)
            delta = event.get("delta")
            if not isinstance(delta, dict):
                return None

            delta_type = delta.get("type")
            if delta_type == "text_delta":
                chunk = delta.get("text")
                if not isinstance(chunk, str) or chunk == "":
                    return None
                block = self._ensure_block(index, "text")
                block["type"] = "text"
                block["text"] = f"{block.get('text', '')}{chunk}"
                return {
                    "session_id": self._session_id,
                    "parent_tool_use_id": self._parent_tool_use_id,
                    "event_type": "content_block_delta",
                    "delta_type": "text_delta",
                    "block_index": index,
                    "text": chunk,
                }

            if delta_type == "input_json_delta":
                chunk = delta.get("partial_json")
                if not isinstance(chunk, str) or chunk == "":
                    return None
                block = self._ensure_block(index, "tool_use")
                block["type"] = "tool_use"
                if not isinstance(block.get("input"), dict):
                    block["input"] = {}

                current_json = self._tool_input_json.get(index, "")
                updated_json = f"{current_json}{chunk}"
                self._tool_input_json[index] = updated_json

                parsed = _safe_json_parse(updated_json)
                if isinstance(parsed, dict):
                    block["input"] = parsed

                return {
                    "session_id": self._session_id,
                    "parent_tool_use_id": self._parent_tool_use_id,
                    "event_type": "content_block_delta",
                    "delta_type": "input_json_delta",
                    "block_index": index,
                    "partial_json": chunk,
                }

            if delta_type == "thinking_delta":
                chunk = delta.get("thinking")
                if not isinstance(chunk, str) or chunk == "":
                    return None
                block = self._ensure_block(index, "thinking")
                block["type"] = "thinking"
                block["thinking"] = f"{block.get('thinking', '')}{chunk}"
                return {
                    "session_id": self._session_id,
                    "parent_tool_use_id": self._parent_tool_use_id,
                    "event_type": "content_block_delta",
                    "delta_type": "thinking_delta",
                    "block_index": index,
                    "thinking": chunk,
                }

        return None

    def build_turn(self) -> Optional[dict[str, Any]]:
        """Build current draft assistant turn for rendering."""
        if not self._blocks_by_index:
            return None

        ordered_blocks = [
            copy.deepcopy(self._blocks_by_index[index])
            for index in sorted(self._blocks_by_index)
        ]

        has_visible_content = False
        for block in ordered_blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text" and str(block.get("text", "")).strip():
                has_visible_content = True
                break
            if block_type == "thinking" and str(block.get("thinking", "")).strip():
                has_visible_content = True
                break
            if block_type == "tool_use":
                has_visible_content = True
                break

        if not has_visible_content:
            return None

        draft_id = self._session_id or "unknown"
        return normalize_turn({
            "type": "assistant",
            "content": ordered_blocks,
            "uuid": f"draft-{draft_id}",
        })


class AssistantStreamProjector:
    """Projects mixed runtime messages into snapshot/patch/delta updates."""

    def __init__(self, initial_messages: Optional[list[dict[str, Any]]] = None):
        self._groupable_messages: list[dict[str, Any]] = []
        self.turns: list[dict[str, Any]] = []
        self.draft = DraftAssistantProjector()
        self.last_result: Optional[dict[str, Any]] = None

        for message in initial_messages or []:
            self.apply_message(message)

    def apply_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """Apply one message and return projector updates."""
        update = {
            "patch": None,
            "delta": None,
            "question": None,
        }

        if not isinstance(message, dict):
            return update

        msg_type = message.get("type")
        if msg_type in _GROUPABLE_TYPES:
            previous_turns = self.turns
            self._groupable_messages.append(message)
            self.turns = group_messages_into_turns(self._groupable_messages)

            if msg_type in {"assistant", "result"}:
                self.draft.clear()
            if msg_type == "result":
                self.last_result = copy.deepcopy(message)

            patch = build_turn_patch(previous_turns, self.turns)
            if patch:
                update["patch"] = {
                    "patch": patch,
                    "draft_turn": self.draft.build_turn(),
                }
            return update

        if msg_type == "stream_event":
            delta = self.draft.apply_stream_event(message)
            if delta:
                delta["draft_turn"] = self.draft.build_turn()
                update["delta"] = delta
            return update

        if msg_type == "ask_user_question":
            update["question"] = copy.deepcopy(message)

        return update

    def build_snapshot(
        self,
        session_id: str,
        status: str,
        pending_questions: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Build unified snapshot payload for API and SSE."""
        return {
            "session_id": session_id,
            "status": status,
            "turns": [normalize_turn(t) for t in self.turns],
            "draft_turn": self.draft.build_turn(),
            "pending_questions": copy.deepcopy(pending_questions or []),
        }
