# Agent Conversation Turn Unified Normalization Design

## Problem

Agent conversation loading has 3 scenarios: real-time conversation, history conversation, and reconnection to an ongoing conversation. Because the data sources differ (JSONL transcript, SDK memory buffer, streaming DraftProjector), the resulting Turn structures have systematic differences, causing inconsistent rendering.

### Data Structure Difference Overview

| Dimension | JSONL Transcript | SDK Buffer | Draft (streaming) |
|-----------|-----------------|------------|------------------|
| `uuid` | Always present | User has it; Assistant/Result **absent** | `"draft-{id}"` synthesized |
| `timestamp` | Always present | **Always missing** | **Missing** |
| `tool_use.result` | Attached by turn_grouper | Attached by turn_grouper | **Always missing** |
| `tool_use.skill_content` | Attached by turn_grouper | Attached by turn_grouper | **Always missing** |
| `tool_use.id` | Real string | Real string | Initially `null` |
| `result` turn | **Not present** (JSONL has no such type) | Present | Not applicable |
| Content format | string or array | string or array | Always array |
| Block `type` | Usually present | May be missing | Always present |

### Root Cause

Normalization is scattered across 4 places, each solving only part of the problem:
1. `turn_grouper._normalize_block()` — block type inference
2. `stream_projector._normalize_block()` — field defaults
3. `service._build_initial_raw_messages()` — deduplication filtering
4. Frontend `ChatMessage.normalizeContent()` — string→array conversion

### Reconnect Message Loss

`_build_initial_raw_messages()` at service.py:451 filters out assistant/result messages that lack a uuid, causing the most recent assistant reply (not yet written to JSONL) to be lost during reconnection.

---

## Approach: Unified Projector-Level Normalization

Share the same normalization logic between `turn_grouper` and `stream_projector` at the source.

### Turn Contract (Output Specification)

```python
Turn = {
    "type": "user" | "assistant" | "system" | "result",
    "content": list[ContentBlock],   # Always an array, never a string
    "uuid": str | None,
    "timestamp": str | None,
}

ContentBlock = {
    "type": str,                     # Always present
    "text": str,                     # Optional, type=text/skill_content
    "thinking": str,                 # Optional, type=thinking
    "id": str | None,                # Optional, type=tool_use (may be None during early streaming)
    "name": str,                     # Optional, type=tool_use (may be "" during early streaming)
    "input": dict,                   # Optional, type=tool_use (always dict)
    "result": str,                   # Optional, type=tool_use (for completed tool calls)
    "is_error": bool,                # Optional, type=tool_use
    "skill_content": str,            # Optional, type=tool_use when name=Skill
}
```

### Shared Module: `turn_schema.py`

Create `server/agent_runtime/turn_schema.py` with shared normalization logic:

```python
def infer_block_type(block: dict) -> str:
    """Infer missing block type."""

def normalize_block(block: dict) -> dict:
    """Unified block normalization."""

def normalize_content(content: Any) -> list[dict]:
    """Convert content to list[dict] always."""

def normalize_turn(turn: dict) -> dict:
    """Ensure Turn satisfies the contract."""

def normalize_turns(turns: list[dict]) -> list[dict]:
    """Batch normalization."""
```

---

## Implementation Steps

### Step 1: Create `turn_schema.py`
- Extract `_infer_block_type()`, `_normalize_block()`, `_normalize_content()` from `turn_grouper.py`
- Add `normalize_turn()`, `normalize_turns()`

### Step 2: Refactor `turn_grouper.py`
- Remove local `_infer_block_type()`, `_normalize_block()`, `_normalize_content()`
- Replace with `from turn_schema import` calls
- Call `normalize_turn()` on each turn before `group_messages_into_turns()` outputs

### Step 3: Refactor `stream_projector.py`
- Replace `DraftAssistantProjector._normalize_block()` with shared implementation
- Call `normalize_turn()` before `build_turn()` outputs
- Retain `_ensure_block()`'s streaming-specific logic (creating empty-shell blocks)

### Step 4: Fix `service.py` Reconnect Message Loss
- Modify the filter logic in `_build_initial_raw_messages()`
- Allow assistant/result messages from the buffer that come after the transcript's last entry to pass through
- Call `normalize_turns()` in `build_snapshot()` and `_emit_running_snapshot()` as the final gate

### Step 5: Simplify Frontend Redundant Normalization
- `ChatMessage.tsx`: simplify `normalizeContent()`, remove JSON parse branch
- `ContentBlockRenderer.tsx`: remove silent fallback (`block.type || "text"`)
- Optional: dev-only Turn contract validation

---

## Files Involved

| File | Action | Risk |
|------|--------|------|
| `server/agent_runtime/turn_schema.py` | **Create** | Low |
| `server/agent_runtime/turn_grouper.py` | Refactor (extract → import) | Medium |
| `server/agent_runtime/stream_projector.py` | Refactor (replace normalize) | Medium |
| `server/agent_runtime/service.py` | Modify filter logic + output normalization | Medium |
| `frontend/src/components/copilot/chat/ChatMessage.tsx` | Simplify | Low |
| `frontend/src/components/copilot/chat/ContentBlockRenderer.tsx` | Simplify | Low |

## Testing Strategy

- Existing turn_grouper tests should continue to pass (behavior unchanged, code location migrated)
- Add `test_turn_schema.py` covering normalization for various input formats
- Manually verify three scenarios: history loading, real-time streaming, reconnection
