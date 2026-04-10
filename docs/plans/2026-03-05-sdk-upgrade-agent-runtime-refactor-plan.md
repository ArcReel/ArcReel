# SDK v0.1.46 Upgrade + Agent Runtime Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Use SDK v0.1.46's `get_session_messages()` and Typed Task Messages to refactor agent_runtime, simplifying transcript reading, turn grouping, and dedup logic.

**Architecture:** Replace the hand-written JSONL parser (TranscriptReader) with SDK `get_session_messages()`, refactor turn_grouper into a multi-pass pipeline, simplify service.py dedup logic from ~100 lines to ~50 lines, and add PR #621 subagent task progress support.

**Tech Stack:** Python 3.12+, claude-agent-sdk v0.1.46, FastAPI, React/TypeScript (htm templates)

**Design Doc:** `docs/plans/2026-03-05-sdk-upgrade-agent-runtime-refactor-design.md`

---

### Task 1: SdkTranscriptAdapter Unit Tests

**Files:**
- Create: `tests/test_sdk_transcript_adapter.py`

**Step 1: Write failing tests for SdkTranscriptAdapter**

```python
"""Unit tests for SdkTranscriptAdapter."""

from unittest.mock import patch, MagicMock
import pytest

from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter


class TestSdkTranscriptAdapter:
    def test_read_raw_messages_returns_adapted_messages(self):
        """SDK messages are adapted to the internal dict format."""
        mock_msg = MagicMock()
        mock_msg.type = "user"
        mock_msg.message = {"content": "Hello"}
        mock_msg.uuid = "uuid-123"
        mock_msg.parent_tool_use_id = None
        mock_msg.timestamp = "2026-03-05T00:00:00Z"

        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            return_value=[mock_msg],
        ):
            adapter = SdkTranscriptAdapter()
            result = adapter.read_raw_messages("sdk-session-123")

        assert len(result) == 1
        assert result[0]["type"] == "user"
        assert result[0]["content"] == "Hello"
        assert result[0]["uuid"] == "uuid-123"

    def test_read_raw_messages_empty_session_id(self):
        """Empty session ID returns empty list."""
        adapter = SdkTranscriptAdapter()
        assert adapter.read_raw_messages("") == []
        assert adapter.read_raw_messages(None) == []

    def test_read_raw_messages_sdk_error_returns_empty(self):
        """SDK exceptions are caught and return empty list."""
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            side_effect=RuntimeError("SDK error"),
        ):
            adapter = SdkTranscriptAdapter()
            assert adapter.read_raw_messages("sdk-session-123") == []

    def test_parent_tool_use_id_preserved(self):
        """parent_tool_use_id is included when present."""
        mock_msg = MagicMock()
        mock_msg.type = "user"
        mock_msg.message = {"content": [{"type": "tool_result", "tool_use_id": "t1"}]}
        mock_msg.uuid = "uuid-456"
        mock_msg.parent_tool_use_id = "task-1"
        mock_msg.timestamp = None

        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            return_value=[mock_msg],
        ):
            adapter = SdkTranscriptAdapter()
            result = adapter.read_raw_messages("sdk-session-123")

        assert result[0]["parent_tool_use_id"] == "task-1"

    def test_exists_returns_true_when_messages_found(self):
        """exists() returns True when session has messages."""
        mock_msg = MagicMock()
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            return_value=[mock_msg],
        ):
            adapter = SdkTranscriptAdapter()
            assert adapter.exists("sdk-session-123") is True

    def test_exists_returns_false_when_no_messages(self):
        """exists() returns False for empty or missing sessions."""
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            return_value=[],
        ):
            adapter = SdkTranscriptAdapter()
            assert adapter.exists("sdk-session-123") is False

    def test_exists_returns_false_on_empty_id(self):
        adapter = SdkTranscriptAdapter()
        assert adapter.exists("") is False
        assert adapter.exists(None) is False

    def test_exists_returns_false_on_sdk_error(self):
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            side_effect=RuntimeError("SDK error"),
        ):
            adapter = SdkTranscriptAdapter()
            assert adapter.exists("sdk-session-123") is False

    def test_assistant_message_content_is_list(self):
        """Assistant messages preserve content as-is (list of blocks)."""
        mock_msg = MagicMock()
        mock_msg.type = "assistant"
        mock_msg.message = {"content": [{"type": "text", "text": "Hello"}]}
        mock_msg.uuid = "uuid-789"
        mock_msg.parent_tool_use_id = None
        mock_msg.timestamp = "2026-03-05T00:00:01Z"

        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            return_value=[mock_msg],
        ):
            adapter = SdkTranscriptAdapter()
            result = adapter.read_raw_messages("sdk-session-123")

        assert result[0]["type"] == "assistant"
        assert result[0]["content"] == [{"type": "text", "text": "Hello"}]
```

**Step 2: Run tests to confirm they fail**

Run: `python -m pytest tests/test_sdk_transcript_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.agent_runtime.sdk_transcript_adapter'`

**Step 3: Commit tests**

```bash
git add tests/test_sdk_transcript_adapter.py
git commit -m "test: add SdkTranscriptAdapter unit tests (red)"
```

---

### Task 2: Implement SdkTranscriptAdapter

**Files:**
- Create: `server/agent_runtime/sdk_transcript_adapter.py`

**Step 1: Write minimum implementation to pass tests**

```python
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
```

**Step 2: Run tests to confirm they pass**

Run: `python -m pytest tests/test_sdk_transcript_adapter.py -v`
Expected: All 9 tests PASS

**Step 3: Commit implementation**

```bash
git add server/agent_runtime/sdk_transcript_adapter.py
git commit -m "feat: add SdkTranscriptAdapter using SDK get_session_messages()"
```

---

### Task 3: Replace TranscriptReader References in service.py

**Files:**
- Modify: `server/agent_runtime/service.py` (lines 23, 41, 458-462)
- Modify: `server/agent_runtime/session_manager.py` (lines 19, 243)

**Step 1: Update service.py imports and initialization**

In `service.py`:
1. Replace `from server.agent_runtime.transcript_reader import TranscriptReader` with `from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter`
2. Replace `self.transcript_reader = TranscriptReader(...)` with `self.transcript_adapter = SdkTranscriptAdapter()`
3. Update the call in `_build_projector`:
   - Old: `self.transcript_reader.read_raw_messages(session_id, meta.sdk_session_id, project_name=meta.project_name)`
   - New: `self.transcript_adapter.read_raw_messages(meta.sdk_session_id)`

**Step 2: Update session_manager.py imports and initialization**

In `session_manager.py`:
1. Remove `from server.agent_runtime.transcript_reader import TranscriptReader` (line 19)
2. Remove `self.transcript_reader = TranscriptReader(data_dir, project_root=project_root)` (line 243)

**Step 3: Run all tests to confirm nothing is broken**

Run: `python -m pytest tests/ -v`
Expected: All tests pass (test_transcript_reader.py still passes as it does not depend on service.py)

**Step 4: Commit changes**

```bash
git add server/agent_runtime/service.py server/agent_runtime/session_manager.py
git commit -m "refactor: replace TranscriptReader with SdkTranscriptAdapter in service.py"
```

---

### Task 4: Add TaskMessage Type Support to session_manager.py

**Files:**
- Modify: `server/agent_runtime/session_manager.py` (lines 226-232, 713-723)

**Step 1: Write tests for TaskMessage handling**

Append to `tests/test_sdk_transcript_adapter.py` or create a separate file:

```python
# tests/test_task_message_types.py
"""Tests for TaskMessage type handling in SessionManager."""

from server.agent_runtime.session_manager import SessionManager


class TestTaskMessageTypes:
    def test_message_type_map_includes_task_messages(self):
        """TaskMessage subclasses map to 'system' type."""
        assert SessionManager._MESSAGE_TYPE_MAP["TaskStartedMessage"] == "system"
        assert SessionManager._MESSAGE_TYPE_MAP["TaskProgressMessage"] == "system"
        assert SessionManager._MESSAGE_TYPE_MAP["TaskNotificationMessage"] == "system"

    def test_task_message_subtypes(self):
        """TaskMessage subtypes are correctly defined."""
        assert SessionManager._TASK_MESSAGE_SUBTYPES["TaskStartedMessage"] == "task_started"
        assert SessionManager._TASK_MESSAGE_SUBTYPES["TaskProgressMessage"] == "task_progress"
        assert SessionManager._TASK_MESSAGE_SUBTYPES["TaskNotificationMessage"] == "task_notification"
```

**Step 2: Run tests to confirm they fail**

Run: `python -m pytest tests/test_task_message_types.py -v`
Expected: FAIL with `AttributeError: type object 'SessionManager' has no attribute '_TASK_MESSAGE_SUBTYPES'`

**Step 3: Update _MESSAGE_TYPE_MAP and add _TASK_MESSAGE_SUBTYPES**

In the `SessionManager` class in `session_manager.py`:

```python
# SDK message class name to type mapping
_MESSAGE_TYPE_MAP = {
    "UserMessage": "user",
    "AssistantMessage": "assistant",
    "ResultMessage": "result",
    "SystemMessage": "system",
    "StreamEvent": "stream_event",
    "TaskStartedMessage": "system",
    "TaskProgressMessage": "system",
    "TaskNotificationMessage": "system",
}

# Typed task message subtypes for precise classification
_TASK_MESSAGE_SUBTYPES = {
    "TaskStartedMessage": "task_started",
    "TaskProgressMessage": "task_progress",
    "TaskNotificationMessage": "task_notification",
}
```

**Step 4: Update _message_to_dict to inject subtype**

After existing logic in the `_message_to_dict` method, add subtype injection:

```python
def _message_to_dict(self, message: Any) -> dict[str, Any]:
    """Convert SDK message to dict for JSON serialization."""
    msg_dict = self._serialize_value(message)

    # Infer and add message type if not present
    if isinstance(msg_dict, dict) and "type" not in msg_dict:
        msg_type = self._infer_message_type(message)
        if msg_type:
            msg_dict["type"] = msg_type

    # Inject precise subtype for typed task messages
    if isinstance(msg_dict, dict):
        class_name = type(message).__name__
        subtype = self._TASK_MESSAGE_SUBTYPES.get(class_name)
        if subtype:
            msg_dict["subtype"] = subtype

    return msg_dict
```

**Step 5: Run tests to confirm they pass**

Run: `python -m pytest tests/test_task_message_types.py -v`
Expected: All tests PASS

**Step 6: Run all tests to confirm nothing is broken**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

**Step 7: Commit changes**

```bash
git add server/agent_runtime/session_manager.py tests/test_task_message_types.py
git commit -m "feat: add TaskMessage type support in SessionManager"
```

---

### Task 5: Eliminate result Turn from turn_grouper

**Files:**
- Modify: `server/agent_runtime/turn_grouper.py` (lines 251-263)
- Modify: `tests/test_turn_grouper.py`

**Step 1: Update test cases to reflect result turn elimination**

In `tests/test_turn_grouper.py`, `test_assistant_messages_merged_and_result_flushed` currently asserts `["user", "assistant", "result"]`. Update it to:

```python
def test_assistant_messages_merged_and_result_flushed(self):
    raw_messages = [
        {"type": "user", "content": "read file"},
        {"type": "assistant", "content": [{"type": "text", "text": "Reading..."}], "uuid": "a1"},
        {
            "type": "assistant",
            "content": [{"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"file_path": "/tmp/a"}}],
            "uuid": "a2",
        },
        {
            "type": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "hello"}],
        },
        {"type": "assistant", "content": [{"type": "text", "text": "Done"}], "uuid": "a3"},
        {"type": "result", "subtype": "success", "uuid": "r1"},
    ]

    turns = group_messages_into_turns(raw_messages)
    # result turn is eliminated - only user and assistant
    assert [turn["type"] for turn in turns] == ["user", "assistant"]
    assistant_turn = turns[1]
    assert len(assistant_turn["content"]) == 3
    assert assistant_turn["content"][0]["type"] == "text"
    assert assistant_turn["content"][1]["type"] == "tool_use"
    assert assistant_turn["content"][1]["result"] == "hello"
    assert assistant_turn["content"][2]["type"] == "text"
```

Add result turn elimination test:

```python
def test_result_turn_is_eliminated(self):
    """Result messages flush current turn but don't create independent turn."""
    raw_messages = [
        {"type": "user", "content": "hello"},
        {"type": "assistant", "content": [{"type": "text", "text": "hi"}]},
        {"type": "result", "subtype": "success"},
    ]
    turns = group_messages_into_turns(raw_messages)
    assert [turn["type"] for turn in turns] == ["user", "assistant"]

def test_result_between_rounds_flushes_correctly(self):
    """Result between two user messages flushes correctly."""
    raw_messages = [
        {"type": "user", "content": "first"},
        {"type": "assistant", "content": [{"type": "text", "text": "response 1"}]},
        {"type": "result", "subtype": "success"},
        {"type": "user", "content": "second"},
        {"type": "assistant", "content": [{"type": "text", "text": "response 2"}]},
    ]
    turns = group_messages_into_turns(raw_messages)
    assert [turn["type"] for turn in turns] == ["user", "assistant", "user", "assistant"]
```

**Step 2: Run tests to confirm they fail**

Run: `python -m pytest tests/test_turn_grouper.py -v`
Expected: FAIL on the modified test (still expects "result" in turn types)

**Step 3: Modify turn_grouper.py to eliminate result turn**

Replace result handling in `group_messages_into_turns` (lines 251-263):

```python
if msg_type == "result":
    if current_turn:
        turns.append(current_turn)
        current_turn = None
    continue  # Don't create independent result turn
```

**Step 4: Run tests to confirm they pass**

Run: `python -m pytest tests/test_turn_grouper.py -v`
Expected: All tests PASS

**Step 5: Sync-update assertions in test_transcript_reader.py**

In `tests/test_transcript_reader.py`'s `test_read_jsonl_transcript_grouped`,
change `assert len(turns) == 3  # user turn, assistant turn, result`
to `assert len(turns) == 2  # user turn, assistant turn (result eliminated)`
and remove the result turn assertions.

**Step 6: Run all tests to confirm they pass**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

**Step 7: Commit changes**

```bash
git add server/agent_runtime/turn_grouper.py tests/test_turn_grouper.py tests/test_transcript_reader.py
git commit -m "refactor: eliminate result turn from turn_grouper output"
```

---

### Task 6: Add task_progress Classification and Handling to turn_grouper

**Files:**
- Modify: `server/agent_runtime/turn_grouper.py`
- Modify: `tests/test_turn_grouper.py`

**Step 1: Write tests for task_progress handling**

Append to `tests/test_turn_grouper.py`:

```python
def test_task_progress_attached_to_assistant_turn(self):
    """Task progress messages are attached as blocks to current assistant turn."""
    raw_messages = [
        {"type": "user", "content": "do something complex"},
        {
            "type": "assistant",
            "content": [{"type": "tool_use", "id": "agent-1", "name": "Agent", "input": {}}],
        },
        {
            "type": "system",
            "subtype": "task_started",
            "description": "Exploring codebase",
            "task_id": "task-abc",
        },
        {
            "type": "system",
            "subtype": "task_notification",
            "description": "Exploring codebase",
            "summary": "Found 3 relevant files",
            "status": "completed",
            "task_id": "task-abc",
        },
    ]
    turns = group_messages_into_turns(raw_messages)
    assert [turn["type"] for turn in turns] == ["user", "assistant"]
    assistant_content = turns[1]["content"]
    # tool_use + 2 task_progress blocks
    assert len(assistant_content) == 3
    assert assistant_content[1]["type"] == "task_progress"
    assert assistant_content[1]["status"] == "task_started"
    assert assistant_content[2]["type"] == "task_progress"
    assert assistant_content[2]["status"] == "task_notification"
    assert assistant_content[2]["task_status"] == "completed"

def test_task_progress_without_assistant_creates_system_turn(self):
    """Task progress without a preceding assistant turn creates a system turn."""
    raw_messages = [
        {"type": "user", "content": "hello"},
        {
            "type": "system",
            "subtype": "task_started",
            "description": "Starting task",
            "task_id": "task-xyz",
        },
    ]
    turns = group_messages_into_turns(raw_messages)
    assert len(turns) == 2
    assert turns[0]["type"] == "user"
    assert turns[1]["type"] == "system"
    assert turns[1]["content"][0]["type"] == "task_progress"
```

**Step 2: Run tests to confirm they fail**

Run: `python -m pytest tests/test_turn_grouper.py::TestTurnGrouper::test_task_progress_attached_to_assistant_turn -v`
Expected: FAIL

**Step 3: Implement task_progress handling**

In `group_messages_into_turns`, after the `msg_type == "assistant"` handling and before the final `continue`, add system/task_progress handling:

```python
if msg_type == "system":
    subtype = msg.get("subtype", "")
    if subtype in ("task_started", "task_progress", "task_notification"):
        task_block = {
            "type": "task_progress",
            "task_id": msg.get("task_id"),
            "status": subtype,
            "description": msg.get("description", ""),
            "summary": msg.get("summary"),
            "task_status": msg.get("status"),
            "usage": msg.get("usage"),
        }
        if current_turn and current_turn.get("type") == "assistant":
            current_turn.get("content", []).append(task_block)
        else:
            if current_turn:
                turns.append(current_turn)
            current_turn = {
                "type": "system",
                "content": [task_block],
                "uuid": msg.get("uuid"),
                "timestamp": msg.get("timestamp"),
            }
        continue
    continue  # Ignore other system subtypes
```

**Step 4: Run tests to confirm they pass**

Run: `python -m pytest tests/test_turn_grouper.py -v`
Expected: All tests PASS

**Step 5: Commit changes**

```bash
git add server/agent_runtime/turn_grouper.py tests/test_turn_grouper.py
git commit -m "feat: add task_progress message handling in turn_grouper"
```

---

### Task 7: Simplify service.py Dedup Logic

**Files:**
- Modify: `server/agent_runtime/service.py` (lines 451-502, 609-790)

**Step 1: Refactor _build_projector and dedup methods**

Refactor `_build_projector` to use UUID set + tail fingerprint strategy:

1. **Delete** `_build_seen_sets` method (lines 673-703)
2. **Delete** `_content_key` method (lines 625-670)
3. **Delete** `_is_duplicate` method (lines 705-720)
4. **Simplify** `_message_key` to UUID lookup only
5. **Add** `_fingerprint_tail` and `_fingerprint` helper methods

Refactored `_build_projector`:

```python
def _build_projector(
    self,
    meta: SessionMeta,
    session_id: str,
    replayed_messages: Optional[list[dict[str, Any]]] = None,
) -> AssistantStreamProjector:
    """Build projector from SDK transcript + in-memory buffer."""
    transcript_msgs = self.transcript_adapter.read_raw_messages(meta.sdk_session_id)
    projector = AssistantStreamProjector(initial_messages=transcript_msgs)

    # UUID set for primary dedup
    transcript_uuids = {m["uuid"] for m in transcript_msgs if m.get("uuid")}

    # Content fingerprints for tail (current round) - fallback dedup
    tail_fps = self._fingerprint_tail(transcript_msgs)

    buffer = replayed_messages
    if buffer is None:
        buffer = self.session_manager.get_buffered_messages(session_id)

    for msg in buffer or []:
        if not isinstance(msg, dict):
            continue
        msg_type = msg.get("type", "")

        # Non-groupable messages pass through directly
        if msg_type not in {"user", "assistant", "result"}:
            projector.apply_message(msg)
            continue

        # 1. UUID dedup
        uuid = msg.get("uuid")
        if uuid and uuid in transcript_uuids:
            continue

        # 2. Local echo dedup
        if msg.get("local_echo") and self._echo_in_transcript(msg, transcript_msgs):
            continue

        # 3. Content fingerprint dedup (fallback for UUID-less buffer messages)
        if not uuid and msg_type in {"assistant", "result"}:
            fp = self._fingerprint(msg)
            if fp and fp in tail_fps:
                continue

        projector.apply_message(msg)

    return projector
```

New helper methods:

```python
@staticmethod
def _fingerprint_tail(messages: list[dict[str, Any]]) -> set[str]:
    """Build content fingerprints for messages after the last real user message."""
    last_user_idx = 0
    for i, msg in enumerate(messages):
        if msg.get("type") == "user":
            content = msg.get("content", "")
            if not (_is_system_injected_user_message(content) or _has_subagent_user_metadata(msg)):
                last_user_idx = i

    fps: set[str] = set()
    for msg in messages[last_user_idx:]:
        fp = AssistantService._fingerprint(msg)
        if fp:
            fps.add(fp)
    return fps

@staticmethod
def _fingerprint(message: dict[str, Any]) -> Optional[str]:
    """Build a truncated content fingerprint for dedup."""
    msg_type = message.get("type")
    if msg_type == "assistant":
        content = message.get("content", [])
        parts: list[str] = []
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            tool_id = block.get("id")
            thinking = block.get("thinking")
            if text is not None:
                parts.append(f"t:{text[:200]}")
            elif tool_id is not None:
                parts.append(f"u:{tool_id}")
            elif thinking is not None:
                parts.append(f"th:{thinking[:200]}")
        return f"fp:assistant:{'/'.join(parts)}" if parts else None
    if msg_type == "result":
        return f"fp:result:{message.get('subtype', '')}:{message.get('is_error', False)}"
    return None

@staticmethod
def _echo_in_transcript(
    echo_msg: dict[str, Any],
    transcript_msgs: list[dict[str, Any]],
) -> bool:
    """Check if a local echo has a matching real message in transcript."""
    echo_text = AssistantService._extract_plain_user_content(echo_msg)
    if not echo_text:
        return False
    for existing in reversed(transcript_msgs):
        if existing.get("type") != "user":
            continue
        existing_text = AssistantService._extract_plain_user_content(existing)
        if existing_text == echo_text:
            return True
    return False
```

**Step 2: Run all tests to confirm they pass**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

**Step 3: Commit changes**

```bash
git add server/agent_runtime/service.py
git commit -m "refactor: simplify service.py dedup logic with UUID sets + tail fingerprint"
```

---

### Task 8: Frontend Type Updates

**Files:**
- Modify: `frontend/src/types/assistant.ts` (lines 22-42)

**Step 1: Update ContentBlock types**

Add `"task_progress"` to the ContentBlock.type union type, add task_progress-related fields:

```typescript
export interface ContentBlock {
  type: "text" | "thinking" | "tool_use" | "tool_result" | "skill_content" | "task_progress";
  text?: string;
  thinking?: string;
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
  result?: string;
  is_error?: boolean;
  skill_content?: string;
  tool_use_id?: string;
  content?: string;
  // task_progress fields
  task_id?: string;
  status?: string;
  description?: string;
  summary?: string;
  task_status?: string;
  usage?: { total_tokens?: number; tool_uses?: number; duration_ms?: number };
}
```

**Step 2: Update Turn type to remove "result"**

```typescript
export interface Turn {
  type: "user" | "assistant" | "system";
  content: ContentBlock[];
  uuid?: string;
  timestamp?: string;
  subtype?: string;
}
```

**Step 3: Commit changes**

```bash
git add frontend/src/types/assistant.ts
git commit -m "feat: update frontend types for task_progress and remove result turn"
```

---

### Task 9: Frontend TaskProgressBlock Component

**Files:**
- Create: `frontend/src/components/copilot/chat/TaskProgressBlock.tsx`
- Modify: `frontend/src/components/copilot/chat/ContentBlockRenderer.tsx`

**Step 1: Create TaskProgressBlock component**

```tsx
import type { ContentBlock } from "@/types";

interface TaskProgressBlockProps {
  block: ContentBlock;
}

export function TaskProgressBlock({ block }: TaskProgressBlockProps) {
  const status = block.status;
  const description = block.description || "";
  const summary = block.summary || "";
  const taskStatus = block.task_status;

  if (status === "task_started") {
    return (
      <div className="my-1 flex items-center gap-1.5 text-xs text-slate-400">
        <span className="inline-block h-3 w-3 animate-spin rounded-full border border-slate-500 border-t-transparent" />
        <span>Subagent started: {description}</span>
      </div>
    );
  }

  if (status === "task_progress") {
    const tokens = block.usage?.total_tokens;
    return (
      <div className="my-1 flex items-center gap-1.5 text-xs text-slate-400">
        <span className="inline-block h-3 w-3 animate-spin rounded-full border border-slate-500 border-t-transparent" />
        <span>
          {description}
          {tokens != null && ` (tokens: ${tokens})`}
        </span>
      </div>
    );
  }

  if (status === "task_notification") {
    const isCompleted = taskStatus === "completed";
    const isFailed = taskStatus === "failed";
    return (
      <div
        className={`my-1 flex items-center gap-1.5 text-xs ${
          isFailed ? "text-red-400" : isCompleted ? "text-green-400" : "text-slate-400"
        }`}
      >
        <span>{isCompleted ? "V" : isFailed ? "X" : "-"}</span>
        <span>
          Subagent {isCompleted ? "completed" : isFailed ? "failed" : "ended"}: {summary || description}
        </span>
      </div>
    );
  }

  return null;
}
```

**Step 2: Update ContentBlockRenderer to add task_progress case**

Add to the switch in `ContentBlockRenderer.tsx`:

```tsx
import { TaskProgressBlock } from "./TaskProgressBlock";

// ... inside switch:
case "task_progress":
  return (
    <TaskProgressBlock
      key={block.id ?? `block-${index}`}
      block={block}
    />
  );
```

**Step 3: Run frontend build to confirm compilation succeeds**

Run: `cd frontend && pnpm build`
Expected: Build success

**Step 4: Commit changes**

```bash
git add frontend/src/components/copilot/chat/TaskProgressBlock.tsx frontend/src/components/copilot/chat/ContentBlockRenderer.tsx
git commit -m "feat: add TaskProgressBlock component for sub-agent task progress"
```

---

### Task 10: Cleanup Legacy Code and Final Verification

**Files:**
- Delete content: `server/agent_runtime/transcript_reader.py` (retain file but mark as deprecated, or delete directly)
- Modify: `tests/test_transcript_reader.py` (update or mark)

**Step 1: Confirm TranscriptReader has no runtime references**

Search for all `TranscriptReader` and `transcript_reader` references; confirm only test files and the module itself have references.

Run: `grep -r "TranscriptReader\|transcript_reader" server/ --include="*.py"`
Expected: No results (session_manager.py and service.py have already removed references)

**Step 2: Retain TranscriptReader and its tests; add deprecation comment**

Add to the top of `transcript_reader.py`:
```python
# DEPRECATED: Replaced by SdkTranscriptAdapter in v0.1.46 upgrade.
# Kept for reference during migration period. Safe to delete after verification.
```

**Step 3: Run all backend tests**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

**Step 4: Run frontend build**

Run: `cd frontend && pnpm build`
Expected: Build success

**Step 5: Run frontend tests (if any)**

Run: `cd frontend && node --test tests/`
Expected: All tests PASS

**Step 6: Commit final cleanup**

```bash
git add server/agent_runtime/transcript_reader.py
git commit -m "chore: mark TranscriptReader as deprecated (replaced by SdkTranscriptAdapter)"
```

---

### Task 11: Final Integration Commit

**Step 1: Confirm git status is clean**

Run: `git status`
Expected: Clean working tree

**Step 2: View full commit history**

Run: `git log --oneline feat/update-agent-sdk-to-0.1.46 ^main`
Expected: Clean, well-structured commit sequence

---

## File Change Summary

| File | Action | Task |
|------|------|------|
| `server/agent_runtime/sdk_transcript_adapter.py` | Create | Task 2 |
| `server/agent_runtime/service.py` | Modify | Task 3, 7 |
| `server/agent_runtime/session_manager.py` | Modify | Task 3, 4 |
| `server/agent_runtime/turn_grouper.py` | Modify | Task 5, 6 |
| `server/agent_runtime/transcript_reader.py` | Mark deprecated | Task 10 |
| `frontend/src/types/assistant.ts` | Modify | Task 8 |
| `frontend/src/components/copilot/chat/TaskProgressBlock.tsx` | Create | Task 9 |
| `frontend/src/components/copilot/chat/ContentBlockRenderer.tsx` | Modify | Task 9 |
| `tests/test_sdk_transcript_adapter.py` | Create | Task 1 |
| `tests/test_task_message_types.py` | Create | Task 4 |
| `tests/test_turn_grouper.py` | Modify | Task 5, 6 |
| `tests/test_transcript_reader.py` | Modify | Task 5 |
