"""
Shared projector for assistant snapshots and live streaming updates.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from server.agent_runtime.turn_grouper import build_turn_patch, group_messages_into_turns
from server.agent_runtime.turn_schema import (
    normalize_block as _shared_normalize_block,
)
from server.agent_runtime.turn_schema import (
    normalize_turn,
)

_GROUPABLE_TYPES = {"user", "assistant", "result", "system"}


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


def _is_ask_user_question_block(block: Any) -> bool:
    """Return True when a block is an AskUserQuestion tool_use block."""
    if not isinstance(block, dict):
        return False
    return block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion"


def _get_ask_user_question_signature(block: dict[str, Any]) -> str | None:
    """Build a stable signature for AskUserQuestion blocks."""
    block_id = block.get("id")
    if isinstance(block_id, str) and block_id:
        return f"id:{block_id}"

    input_payload = block.get("input")
    if not isinstance(input_payload, dict):
        return None

    questions = input_payload.get("questions")
    if not isinstance(questions, list) or not questions:
        return None

    try:
        return f"questions:{json.dumps(questions, ensure_ascii=False, sort_keys=True)}"
    except (TypeError, ValueError):
        return None


def _canonicalize_block_for_dedupe(
    block: Any,
    *,
    include_tool_result_state: bool = True,
) -> dict[str, Any]:
    """Reduce a block to the user-visible fields used for duplicate detection."""
    normalized = _shared_normalize_block(block)
    canonical: dict[str, Any] = {"type": normalized.get("type", "")}

    for key in (
        "text",
        "thinking",
        "id",
        "name",
        "skill_content",
        "tool_use_id",
        "content",
    ):
        if key in normalized:
            canonical[key] = copy.deepcopy(normalized[key])

    if include_tool_result_state:
        for key in ("result", "is_error"):
            if key in normalized:
                canonical[key] = copy.deepcopy(normalized[key])

    if isinstance(normalized.get("input"), dict):
        canonical["input"] = copy.deepcopy(normalized["input"])

    return canonical


def _find_last_assistant_turn(turns: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the last assistant turn, skipping trailing system turns."""
    for turn in reversed(turns):
        if isinstance(turn, dict) and turn.get("type") == "assistant":
            return turn
    return None


def _draft_matches_last_assistant_turn(
    turns: list[dict[str, Any]],
    draft_turn: dict[str, Any] | None,
    *,
    include_tool_result_state: bool = True,
) -> bool:
    """Return True when the reconnect draft repeats the last committed assistant turn."""
    if not isinstance(draft_turn, dict):
        return False

    last_turn = _find_last_assistant_turn(turns)
    if last_turn is None:
        return False

    draft_blocks = draft_turn.get("content")
    last_turn_blocks = last_turn.get("content")
    if not isinstance(draft_blocks, list) or not isinstance(last_turn_blocks, list):
        return False

    return [
        _canonicalize_block_for_dedupe(
            block,
            include_tool_result_state=include_tool_result_state,
        )
        for block in draft_blocks
    ] == [
        _canonicalize_block_for_dedupe(
            block,
            include_tool_result_state=include_tool_result_state,
        )
        for block in last_turn_blocks
    ]


def _draft_is_contiguous_slice_of_last_assistant_turn(
    turns: list[dict[str, Any]],
    draft_turn: dict[str, Any] | None,
    *,
    include_tool_result_state: bool = True,
) -> bool:
    """Return True when the draft repeats any contiguous slice of the last assistant turn.

    Covers prefix, middle, and suffix matches — the draft's stream events may
    start later than the committed turn (missing thinking/early tool blocks)
    and end earlier (task_progress appended after the draft was built).
    """
    if not isinstance(draft_turn, dict):
        return False

    last_turn = _find_last_assistant_turn(turns)
    if last_turn is None:
        return False

    draft_blocks = draft_turn.get("content")
    last_turn_blocks = last_turn.get("content")
    if not isinstance(draft_blocks, list) or not isinstance(last_turn_blocks, list):
        return False
    if not draft_blocks or len(draft_blocks) >= len(last_turn_blocks):
        return False

    canonical_draft = [
        _canonicalize_block_for_dedupe(
            block,
            include_tool_result_state=include_tool_result_state,
        )
        for block in draft_blocks
    ]
    canonical_committed = [
        _canonicalize_block_for_dedupe(
            block,
            include_tool_result_state=include_tool_result_state,
        )
        for block in last_turn_blocks
    ]

    n = len(canonical_draft)
    return any(canonical_committed[i : i + n] == canonical_draft for i in range(len(canonical_committed) - n + 1))


def _hide_stale_draft_turn(
    turns: list[dict[str, Any]],
    draft_turn: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Hide stale drafts that would duplicate the last committed assistant turn."""
    if not isinstance(draft_turn, dict):
        return None

    if _draft_matches_last_assistant_turn(turns, draft_turn):
        return None
    if _draft_is_contiguous_slice_of_last_assistant_turn(turns, draft_turn):
        return None

    # When an AskUserQuestion answer arrives, the committed assistant turn gains
    # tool_use.result/is_error before the next continuation text starts streaming.
    # The in-flight draft may still hold the pre-result copy of that same turn.
    if _draft_matches_last_assistant_turn(
        turns,
        draft_turn,
        include_tool_result_state=False,
    ):
        return None
    if _draft_is_contiguous_slice_of_last_assistant_turn(
        turns,
        draft_turn,
        include_tool_result_state=False,
    ):
        return None

    draft_blocks = draft_turn.get("content")
    if not isinstance(draft_blocks, list) or not draft_blocks:
        return draft_turn
    if not all(_is_ask_user_question_block(block) for block in draft_blocks):
        return draft_turn

    last_turn = turns[-1] if turns else None
    if not isinstance(last_turn, dict):
        return draft_turn

    last_turn_blocks = last_turn.get("content")
    if not isinstance(last_turn_blocks, list):
        return draft_turn

    last_turn_signatures = {
        signature
        for signature in (
            _get_ask_user_question_signature(block) for block in last_turn_blocks if _is_ask_user_question_block(block)
        )
        if signature
    }
    if not last_turn_signatures:
        return draft_turn

    draft_signatures = [
        signature for signature in (_get_ask_user_question_signature(block) for block in draft_blocks) if signature
    ]
    if not draft_signatures:
        return draft_turn

    if all(signature in last_turn_signatures for signature in draft_signatures):
        return None

    return draft_turn


class DraftAssistantProjector:
    """Builds an in-flight assistant turn from StreamEvent payloads."""

    def __init__(self):
        self._blocks_by_index: dict[int, dict[str, Any]] = {}
        self._tool_input_json: dict[int, str] = {}
        self._session_id: str | None = None
        self._parent_tool_use_id: str | None = None

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

    def apply_stream_event(self, stream_message: dict[str, Any]) -> dict[str, Any] | None:
        """Apply one stream_event message and return a delta payload when applicable."""
        event = stream_message.get("event")
        if not isinstance(event, dict):
            return None

        self._session_id = stream_message.get("session_id") or self._session_id
        self._parent_tool_use_id = stream_message.get("parent_tool_use_id") or self._parent_tool_use_id

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

    def build_turn(self) -> dict[str, Any] | None:
        """Build current draft assistant turn for rendering."""
        if not self._blocks_by_index:
            return None

        ordered_blocks = [copy.deepcopy(self._blocks_by_index[index]) for index in sorted(self._blocks_by_index)]

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
        return normalize_turn(
            {
                "type": "assistant",
                "content": ordered_blocks,
                "uuid": f"draft-{draft_id}",
            }
        )


class AssistantStreamProjector:
    """Projects mixed runtime messages into snapshot/patch/delta updates."""

    def __init__(self, initial_messages: list[dict[str, Any]] | None = None):
        self._groupable_messages: list[dict[str, Any]] = []
        self.turns: list[dict[str, Any]] = []
        self.draft = DraftAssistantProjector()
        self.last_result: dict[str, Any] | None = None

        if initial_messages:
            self._batch_init(initial_messages)

    def _batch_init(self, messages: list[dict[str, Any]]) -> None:
        """Batch-process initial messages in O(n) instead of per-message apply."""
        groupable: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            msg_type = message.get("type")
            if msg_type in _GROUPABLE_TYPES:
                groupable.append(message)
                if msg_type == "result":
                    self.last_result = copy.deepcopy(message)
        self._groupable_messages = groupable
        self.turns = group_messages_into_turns(groupable) if groupable else []

    def _build_visible_draft_turn(self) -> dict[str, Any] | None:
        """Build the current draft turn and hide reconnect/resume duplicates."""
        # self.turns are already normalized by group_messages_into_turns
        return _hide_stale_draft_turn(
            self.turns,
            self.draft.build_turn(),
        )

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
                    "draft_turn": self._build_visible_draft_turn(),
                }
            return update

        if msg_type == "stream_event":
            delta = self.draft.apply_stream_event(message)
            if delta:
                delta["draft_turn"] = self._build_visible_draft_turn()
                update["delta"] = delta
            return update

        if msg_type == "ask_user_question":
            update["question"] = copy.deepcopy(message)

        return update

    def build_snapshot(
        self,
        session_id: str,
        status: str,
        pending_questions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Build unified snapshot payload for API and SSE."""
        # self.turns are already normalized by group_messages_into_turns
        return {
            "session_id": session_id,
            "status": status,
            "turns": self.turns,
            "draft_turn": self._build_visible_draft_turn(),
            "pending_questions": pending_questions or [],
        }
