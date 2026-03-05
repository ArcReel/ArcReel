# SDK v0.1.46 升级 + Agent Runtime 重构实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 利用 SDK v0.1.46 的 `get_session_messages()` 和 Typed Task Messages 重构 agent_runtime，简化 transcript 读取、turn 分组和去重逻辑。

**Architecture:** 用 SDK `get_session_messages()` 替换手写 JSONL 解析器（TranscriptReader），将 turn_grouper 重构为多 Pass 管线，简化 service.py 的去重逻辑从 ~100 行到 ~50 行，新增 PR #621 子代理任务进度支持。

**Tech Stack:** Python 3.12+, claude-agent-sdk v0.1.46, FastAPI, React/TypeScript (htm 模板)

**设计文档:** `docs/plans/2026-03-05-sdk-upgrade-agent-runtime-refactor-design.md`

---

### Task 1: SdkTranscriptAdapter 单元测试

**Files:**
- Create: `tests/test_sdk_transcript_adapter.py`

**Step 1: 编写 SdkTranscriptAdapter 的 failing tests**

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

**Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_sdk_transcript_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.agent_runtime.sdk_transcript_adapter'`

**Step 3: 提交测试**

```bash
git add tests/test_sdk_transcript_adapter.py
git commit -m "test: add SdkTranscriptAdapter unit tests (red)"
```

---

### Task 2: 实现 SdkTranscriptAdapter

**Files:**
- Create: `server/agent_runtime/sdk_transcript_adapter.py`

**Step 1: 编写最小实现使测试通过**

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

**Step 2: 运行测试确认通过**

Run: `python -m pytest tests/test_sdk_transcript_adapter.py -v`
Expected: All 9 tests PASS

**Step 3: 提交实现**

```bash
git add server/agent_runtime/sdk_transcript_adapter.py
git commit -m "feat: add SdkTranscriptAdapter using SDK get_session_messages()"
```

---

### Task 3: 替换 service.py 中的 TranscriptReader 引用

**Files:**
- Modify: `server/agent_runtime/service.py` (lines 23, 41, 458-462)
- Modify: `server/agent_runtime/session_manager.py` (lines 19, 243)

**Step 1: 更新 service.py 的 import 和初始化**

在 `service.py` 中：
1. 替换 `from server.agent_runtime.transcript_reader import TranscriptReader` 为 `from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter`
2. 将 `self.transcript_reader = TranscriptReader(...)` 替换为 `self.transcript_adapter = SdkTranscriptAdapter()`
3. 更新 `_build_projector` 中的调用：
   - 旧: `self.transcript_reader.read_raw_messages(session_id, meta.sdk_session_id, project_name=meta.project_name)`
   - 新: `self.transcript_adapter.read_raw_messages(meta.sdk_session_id)`

**Step 2: 更新 session_manager.py 的 import 和初始化**

在 `session_manager.py` 中：
1. 删除 `from server.agent_runtime.transcript_reader import TranscriptReader`（line 19）
2. 删除 `self.transcript_reader = TranscriptReader(data_dir, project_root=project_root)`（line 243）

**Step 3: 运行全部测试确认没有破坏**

Run: `python -m pytest tests/ -v`
Expected: 所有测试通过（test_transcript_reader.py 仍通过因为不依赖 service.py）

**Step 4: 提交变更**

```bash
git add server/agent_runtime/service.py server/agent_runtime/session_manager.py
git commit -m "refactor: replace TranscriptReader with SdkTranscriptAdapter in service.py"
```

---

### Task 4: session_manager.py 新增 TaskMessage 类型支持

**Files:**
- Modify: `server/agent_runtime/session_manager.py` (lines 226-232, 713-723)

**Step 1: 编写 TaskMessage 处理的测试**

追加到 `tests/test_sdk_transcript_adapter.py` 或创建单独文件：

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

**Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_task_message_types.py -v`
Expected: FAIL with `AttributeError: type object 'SessionManager' has no attribute '_TASK_MESSAGE_SUBTYPES'`

**Step 3: 更新 _MESSAGE_TYPE_MAP 和添加 _TASK_MESSAGE_SUBTYPES**

在 `session_manager.py` 的 `SessionManager` 类中：

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

**Step 4: 更新 _message_to_dict 注入 subtype**

在 `_message_to_dict` 方法中，现有逻辑之后添加 subtype 注入：

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

**Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_task_message_types.py -v`
Expected: All tests PASS

**Step 6: 运行全部测试确认没有破坏**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

**Step 7: 提交变更**

```bash
git add server/agent_runtime/session_manager.py tests/test_task_message_types.py
git commit -m "feat: add TaskMessage type support in SessionManager"
```

---

### Task 5: turn_grouper 消除 result turn

**Files:**
- Modify: `server/agent_runtime/turn_grouper.py` (lines 251-263)
- Modify: `tests/test_turn_grouper.py`

**Step 1: 更新测试用例以反映 result turn 消除**

在 `tests/test_turn_grouper.py` 中，`test_assistant_messages_merged_and_result_flushed` 当前断言 `["user", "assistant", "result"]`。更新为：

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

新增 result turn 消除测试：

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

**Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_turn_grouper.py -v`
Expected: FAIL on the modified test (still expects "result" in turn types)

**Step 3: 修改 turn_grouper.py 消除 result turn**

替换 `group_messages_into_turns` 中的 result 处理（lines 251-263）：

```python
if msg_type == "result":
    if current_turn:
        turns.append(current_turn)
        current_turn = None
    continue  # Don't create independent result turn
```

**Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_turn_grouper.py -v`
Expected: All tests PASS

**Step 5: 同步更新 test_transcript_reader.py 中的相关断言**

在 `tests/test_transcript_reader.py` 的 `test_read_jsonl_transcript_grouped` 中，
将 `assert len(turns) == 3  # user turn, assistant turn, result`
更新为 `assert len(turns) == 2  # user turn, assistant turn (result eliminated)`
并删除 result turn 的断言。

**Step 6: 运行全部测试确认通过**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

**Step 7: 提交变更**

```bash
git add server/agent_runtime/turn_grouper.py tests/test_turn_grouper.py tests/test_transcript_reader.py
git commit -m "refactor: eliminate result turn from turn_grouper output"
```

---

### Task 6: turn_grouper 新增 task_progress 分类和处理

**Files:**
- Modify: `server/agent_runtime/turn_grouper.py`
- Modify: `tests/test_turn_grouper.py`

**Step 1: 编写 task_progress 处理的测试**

追加到 `tests/test_turn_grouper.py`：

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

**Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_turn_grouper.py::TestTurnGrouper::test_task_progress_attached_to_assistant_turn -v`
Expected: FAIL

**Step 3: 实现 task_progress 处理**

在 `group_messages_into_turns` 中，`msg_type == "assistant"` 处理后、末尾的 `continue` 前，添加 system/task_progress 处理：

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

**Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_turn_grouper.py -v`
Expected: All tests PASS

**Step 5: 提交变更**

```bash
git add server/agent_runtime/turn_grouper.py tests/test_turn_grouper.py
git commit -m "feat: add task_progress message handling in turn_grouper"
```

---

### Task 7: 简化 service.py 去重逻辑

**Files:**
- Modify: `server/agent_runtime/service.py` (lines 451-502, 609-790)

**Step 1: 重构 _build_projector 和去重方法**

将 `_build_projector` 重构为使用 UUID 集合 + tail fingerprint 策略：

1. **删除** `_build_seen_sets` 方法（lines 673-703）
2. **删除** `_content_key` 方法（lines 625-670）
3. **删除** `_is_duplicate` 方法（lines 705-720）
4. **简化** `_message_key` 为仅 UUID 查找
5. **新增** `_fingerprint_tail` 和 `_fingerprint` 辅助方法

重构后的 `_build_projector`：

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

新增辅助方法：

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

**Step 2: 运行全部测试确认通过**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

**Step 3: 提交变更**

```bash
git add server/agent_runtime/service.py
git commit -m "refactor: simplify service.py dedup logic with UUID sets + tail fingerprint"
```

---

### Task 8: 前端类型更新

**Files:**
- Modify: `frontend/src/types/assistant.ts` (lines 22-42)

**Step 1: 更新 ContentBlock 类型**

添加 `"task_progress"` 到 ContentBlock.type 联合类型，添加 task_progress 相关字段：

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

**Step 2: 更新 Turn 类型移除 "result"**

```typescript
export interface Turn {
  type: "user" | "assistant" | "system";
  content: ContentBlock[];
  uuid?: string;
  timestamp?: string;
  subtype?: string;
}
```

**Step 3: 提交变更**

```bash
git add frontend/src/types/assistant.ts
git commit -m "feat: update frontend types for task_progress and remove result turn"
```

---

### Task 9: 前端 TaskProgressBlock 组件

**Files:**
- Create: `frontend/src/components/copilot/chat/TaskProgressBlock.tsx`
- Modify: `frontend/src/components/copilot/chat/ContentBlockRenderer.tsx`

**Step 1: 创建 TaskProgressBlock 组件**

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
        <span>子任务开始: {description}</span>
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
          子任务{isCompleted ? "完成" : isFailed ? "失败" : "结束"}: {summary || description}
        </span>
      </div>
    );
  }

  return null;
}
```

**Step 2: 更新 ContentBlockRenderer 添加 task_progress case**

在 `ContentBlockRenderer.tsx` 的 switch 中添加：

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

**Step 3: 运行前端构建确认编译通过**

Run: `cd frontend && pnpm build`
Expected: Build success

**Step 4: 提交变更**

```bash
git add frontend/src/components/copilot/chat/TaskProgressBlock.tsx frontend/src/components/copilot/chat/ContentBlockRenderer.tsx
git commit -m "feat: add TaskProgressBlock component for sub-agent task progress"
```

---

### Task 10: 清理遗留代码和最终验证

**Files:**
- Delete content: `server/agent_runtime/transcript_reader.py` (保留文件但标记废弃，或直接删除)
- Modify: `tests/test_transcript_reader.py` (更新或标记)

**Step 1: 确认 TranscriptReader 不再有运行时引用**

搜索所有 `TranscriptReader` 和 `transcript_reader` 引用，确认仅测试文件和自身有引用。

Run: `grep -r "TranscriptReader\|transcript_reader" server/ --include="*.py"`
Expected: 无结果（session_manager.py 和 service.py 已移除引用）

**Step 2: 保留 TranscriptReader 及其测试，添加废弃注释**

在 `transcript_reader.py` 顶部添加：
```python
# DEPRECATED: Replaced by SdkTranscriptAdapter in v0.1.46 upgrade.
# Kept for reference during migration period. Safe to delete after verification.
```

**Step 3: 运行全部后端测试**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

**Step 4: 运行前端构建**

Run: `cd frontend && pnpm build`
Expected: Build success

**Step 5: 运行前端测试（如有）**

Run: `cd frontend && node --test tests/`
Expected: All tests PASS

**Step 6: 提交最终清理**

```bash
git add server/agent_runtime/transcript_reader.py
git commit -m "chore: mark TranscriptReader as deprecated (replaced by SdkTranscriptAdapter)"
```

---

### Task 11: 最终集成提交

**Step 1: 确认 git 状态干净**

Run: `git status`
Expected: Clean working tree

**Step 2: 查看完整提交历史**

Run: `git log --oneline feat/update-agent-sdk-to-0.1.46 ^main`
Expected: 清晰的提交序列

---

## 文件变更汇总

| 文件 | 操作 | Task |
|------|------|------|
| `server/agent_runtime/sdk_transcript_adapter.py` | 新建 | Task 2 |
| `server/agent_runtime/service.py` | 修改 | Task 3, 7 |
| `server/agent_runtime/session_manager.py` | 修改 | Task 3, 4 |
| `server/agent_runtime/turn_grouper.py` | 修改 | Task 5, 6 |
| `server/agent_runtime/transcript_reader.py` | 废弃标记 | Task 10 |
| `frontend/src/types/assistant.ts` | 修改 | Task 8 |
| `frontend/src/components/copilot/chat/TaskProgressBlock.tsx` | 新建 | Task 9 |
| `frontend/src/components/copilot/chat/ContentBlockRenderer.tsx` | 修改 | Task 9 |
| `tests/test_sdk_transcript_adapter.py` | 新建 | Task 1 |
| `tests/test_task_message_types.py` | 新建 | Task 4 |
| `tests/test_turn_grouper.py` | 修改 | Task 5, 6 |
| `tests/test_transcript_reader.py` | 修改 | Task 5 |
