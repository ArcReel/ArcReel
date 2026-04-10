# SDK v0.1.46 Upgrade + Agent Runtime Refactor Design

Date: 2026-03-05
Status: Pending Implementation

## Background

Upgrade `claude-agent-sdk` to v0.1.46 and use features from two new PRs to refactor the agent_runtime module:

- **PR #621**: Typed Task Messages — `TaskStartedMessage`/`TaskProgressMessage`/`TaskNotificationMessage` as `SystemMessage` subclasses
- **PR #622**: `get_session_messages()` / `list_sessions()` — session reading based on `parentUuid` chain reconstruction

## Empirical Data

### `get_session_messages()` Behavior

| Dimension | Result |
|-----------|--------|
| tool_result user messages | **Retained** — does not filter system-injected user messages |
| Branch/sidechain messages | **Filtered** — only main chain retained via parentUuid reconstruction |
| result entries | Not present in JSONL (SessionManager-managed sessions don't write result) |
| compaction | **Handled correctly** — 1487 entries → 12 post-compaction messages |
| UUID availability | All SessionMessages have uuid (required field) |

### SDK Streaming Message UUID Availability

| SDK Message Class | uuid | session_id |
|-------------------|:----:|:----------:|
| UserMessage | ❌ | ❌ |
| AssistantMessage | ❌ | ❌ |
| SystemMessage | ❌ | ❌ |
| TaskStartedMessage (PR #621) | ✅ | ✅ |
| TaskProgressMessage (PR #621) | ✅ | ✅ |
| TaskNotificationMessage (PR #621) | ✅ | ✅ |
| StreamEvent | ✅ | ✅ |
| ResultMessage | ❌ | ✅ |

**Core Constraint**: `UserMessage` and `AssistantMessage` (the types most in need of deduplication) have no uuid. Content-based dedup cannot be eliminated, but it can be simplified.

## Design: Multi-Pass Pipeline + SDK Integration + Pragmatic Dedup

### 1. TranscriptReader → SdkTranscriptAdapter

**Replace** `transcript_reader.py` with `sdk_transcript_adapter.py`.

```python
class SdkTranscriptAdapter:
    """Replaces hand-written JSONL parsing with SDK get_session_messages()."""

    def read_raw_messages(self, sdk_session_id: str) -> list[dict]:
        if not sdk_session_id:
            return []
        try:
            sdk_messages = get_session_messages(sdk_session_id)
        except Exception:
            return []
        return [self._adapt(msg) for msg in sdk_messages]

    def _adapt(self, msg: SessionMessage) -> dict:
        result = {
            "type": msg.type,  # "user" | "assistant"
            "content": msg.message.get("content", ""),
            "uuid": msg.uuid,
        }
        if msg.parent_tool_use_id:
            result["parent_tool_use_id"] = msg.parent_tool_use_id
        return result

    def exists(self, sdk_session_id: str) -> bool:
        if not sdk_session_id:
            return False
        try:
            messages = get_session_messages(sdk_session_id, limit=1)
            return len(messages) > 0
        except Exception:
            return False
```

**Advantages over TranscriptReader**:
- Chain reconstruction (parentUuid): correctly handles branched conversations and compaction; TranscriptReader's linear reading does not
- Filters sidechain/branch messages; TranscriptReader includes all
- Reduces ~180 lines of self-maintained code

**Caveats**:
- `get_session_messages()` is synchronous. The current TranscriptReader is also synchronous (in `_build_projector`), so no new issues are introduced
- It does not return result messages. JSONL also has no result entries, so there is no functional regression
- Skill content messages on non-main branches may be filtered (empirically, 2/6 filtered messages were skill content). This has minimal impact on turn grouping, as these skill contents are redundant on branches anyway

### 2. turn_grouper Multi-Pass Refactor

Split `group_messages_into_turns` from a single function into a multi-pass pipeline.

#### Pass 1: Classify

Identify the semantic type of each message. **Retain** `_is_system_injected_user_message` and `_has_subagent_user_metadata`, because the SDK does not filter tool_result user messages.

```python
def classify_message(msg: dict) -> str:
    """Returns: real_user | system_inject | assistant | task_progress | result"""
    msg_type = msg.get("type", "")
    if msg_type == "assistant":
        return "assistant"
    if msg_type == "result":
        return "result"
    if msg_type == "system":
        subtype = msg.get("subtype", "")
        if subtype in ("task_started", "task_progress", "task_notification"):
            return "task_progress"
        return "system_other"  # compact_boundary etc., ignored
    if msg_type == "user":
        content = msg.get("content", "")
        if _is_system_injected_user_message(content) or _has_subagent_user_metadata(msg):
            return "system_inject"
        return "real_user"
    return "ignore"
```

#### Pass 2: Pair

Attach tool_result/skill_content/task_progress to corresponding assistant blocks.

- `tool_result` → match `tool_use.id` and attach result/is_error (logic unchanged)
- `skill_content` → attach to the nearest Skill tool_use block (logic unchanged)
- `task_progress` → convert to `task_progress` block and attach to current assistant turn (new)

#### Pass 3: Group

Merge consecutive assistant turns; `real_user` starts a new turn.

**Eliminate result turn**: result messages only flush the current_turn; they do not create a separate turn.

```python
if classification == "result":
    if current_turn:
        turns.append(current_turn)
        current_turn = None
    continue  # do not create result turn
```

**Safety**: JSONL has no result entries; result only appears in the runtime buffer. Frontend result turns have no rendered content; removing them has no UI impact.

### 3. Simplified Dedup Strategy

**Current complexity**: ~100 lines (`_build_seen_sets` + `_content_key` + `_is_duplicate` + `_should_skip_local_echo` + round-scoping)

**Simplified to**: ~50 lines

```python
def _build_projector(self, meta, session_id, replayed_messages=None):
    # Step 1: Read transcript via SDK (correct chain, all have UUID)
    transcript_msgs = self._adapter.read_raw_messages(meta.sdk_session_id)

    # Step 2: UUID set
    transcript_uuids = {m["uuid"] for m in transcript_msgs if m.get("uuid")}

    # Step 3: Content fingerprints for current-round tail (messages after last real_user)
    tail_fps = self._fingerprint_tail(transcript_msgs)

    # Step 4: Initialize projector
    projector = AssistantStreamProjector(initial_messages=transcript_msgs)

    # Step 5: Apply buffer with dedup
    buffer = replayed_messages or self.session_manager.get_buffered_messages(session_id)
    for msg in buffer:
        if not isinstance(msg, dict):
            continue
        msg_type = msg.get("type", "")

        # Non-groupable messages pass through directly
        if msg_type not in {"user", "assistant", "result"}:
            projector.apply_message(msg)
            continue

        # (1) UUID dedup
        uuid = msg.get("uuid")
        if uuid and uuid in transcript_uuids:
            continue

        # (2) Local echo dedup
        if msg.get("local_echo") and self._echo_in_transcript(msg, transcript_msgs):
            continue

        # (3) Content fingerprint dedup (fallback)
        if not uuid and msg_type in {"assistant", "result"}:
            fp = self._fingerprint(msg)
            if fp and fp in tail_fps:
                continue

        projector.apply_message(msg)

    return projector
```

**Eliminated complexity**:

| Removed | Reason |
|---------|--------|
| `_build_seen_sets` (last_user_idx tracking) | SDK-returned users are all real users; `_fingerprint_tail` directly finds the last user |
| `_content_key` (MD5 hash of thinking blocks) | Replaced with `_fingerprint`: truncating to 200 chars is sufficient |
| Round-scoping (`seen_content_keys.clear()`) | Naturally bounded to tail (messages after last user) |
| `_is_system_injected_user_message` in dedup | Not needed — SDK users are all real users, no injection detection required |

**Retained logic**:

| Retained | Notes |
|----------|-------|
| UUID set dedup | Main path, unchanged |
| Local echo dedup | Simplified — only searches transcript for matching text |
| Content fingerprint dedup | Only for assistant/result; truncate replaces MD5 |

### 4. PR #621: Subagent Task Progress

#### Backend Message Handling

Add new mappings to `_MESSAGE_TYPE_MAP` in `session_manager.py`:

```python
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
```

Inject precise subtype in `_message_to_dict`:

```python
_TASK_MESSAGE_SUBTYPES = {
    "TaskStartedMessage": "task_started",
    "TaskProgressMessage": "task_progress",
    "TaskNotificationMessage": "task_notification",
}

def _message_to_dict(self, message):
    msg_dict = self._serialize_value(message)
    if isinstance(msg_dict, dict) and "type" not in msg_dict:
        msg_type = self._infer_message_type(message)
        if msg_type:
            msg_dict["type"] = msg_type
    # Inject typed task message subtype
    class_name = type(message).__name__
    subtype = self._TASK_MESSAGE_SUBTYPES.get(class_name)
    if subtype and isinstance(msg_dict, dict):
        msg_dict["subtype"] = subtype
    return msg_dict
```

#### turn_grouper Handling

In Pass 2 (Pair), convert task_progress type messages to blocks:

```python
task_progress_block = {
    "type": "task_progress",
    "task_id": msg.get("task_id"),
    "status": msg.get("subtype"),  # task_started | task_progress | task_notification
    "description": msg.get("description", ""),
    "summary": msg.get("summary"),       # TaskNotificationMessage
    "task_status": msg.get("status"),     # completed | failed | stopped
    "usage": msg.get("usage"),            # TaskUsage dict
}
```

Attach to the content of the current assistant turn. If there is no current assistant turn, create a type="system" turn.

#### Frontend Rendering

Add `TaskProgressBlock` component:

```
ContentBlock.type new value: "task_progress"

TaskProgressBlock rendering:
  task_started  → "Subagent started: {description}"
  task_progress → "{description} (tokens: {usage.total_tokens})"
  task_notification(completed) → "Subagent completed: {summary}"
  task_notification(failed)    → "Subagent failed: {summary}"
```

### 5. Frontend Type Changes

```typescript
// Turn.type: remove "result"
export interface Turn {
  type: "user" | "assistant" | "system";
  content: ContentBlock[];
  uuid?: string;
  timestamp?: string;
}

// ContentBlock.type: add "task_progress"
export interface ContentBlock {
  type: "text" | "thinking" | "tool_use" | "tool_result" | "skill_content" | "task_progress";
  // ... existing fields ...
  // New task_progress fields
  task_id?: string;
  status?: string;
  description?: string;
  summary?: string;
  task_status?: string;
  usage?: { total_tokens?: number; tool_uses?: number; duration_ms?: number };
}
```

## File Change Checklist

| File | Action | Notes |
|------|--------|-------|
| `pyproject.toml` | Modify | Already done: `>=0.1.44` → `>=0.1.46` |
| `server/agent_runtime/transcript_reader.py` | Delete | Replaced by sdk_transcript_adapter.py |
| `server/agent_runtime/sdk_transcript_adapter.py` | Create | SDK get_session_messages() wrapper |
| `server/agent_runtime/turn_grouper.py` | Refactor | Multi-pass pipeline + eliminate result turn + task_progress handling |
| `server/agent_runtime/session_manager.py` | Modify | Add to _MESSAGE_TYPE_MAP + enhance _message_to_dict |
| `server/agent_runtime/service.py` | Modify | Replace TranscriptReader references + simplify dedup |
| `server/agent_runtime/models.py` | Modify | SessionStatus unchanged |
| `frontend/src/types/assistant.ts` | Modify | Remove result from Turn.type + add task_progress to ContentBlock |
| `frontend/src/components/copilot/chat/ContentBlockRenderer.tsx` | Modify | Add task_progress case |
| `frontend/src/components/copilot/chat/TaskProgressBlock.tsx` | Create | Subagent progress rendering component |
| `tests/test_turn_grouper.py` | Modify | Add multi-pass tests + task_progress tests |
| `tests/test_sdk_transcript_adapter.py` | Create | SdkTranscriptAdapter unit tests |

## Risks and Mitigation

| Risk | Mitigation |
|------|-----------|
| SDK `get_session_messages()` filters meaningful skill content on the main branch | Empirically only 2/6 were filtered and all were branch-redundant content; even if occasionally filtered, turn grouping is unaffected (skill content is only decorative) |
| Truncate strategy for content-based dedup may cause false matches | Bounded to the current-round tail; collision probability is extremely low; only used for UUID-less buffer messages |
| Eliminating result turn may affect frontend logic | Empirically result turn has no rendered content; frontend only needs to remove type checks; no functional regression |
| `get_session_messages()` synchronously blocks the event loop | Existing TranscriptReader is also synchronous; no new issues introduced; can be wrapped in run_in_executor later |
