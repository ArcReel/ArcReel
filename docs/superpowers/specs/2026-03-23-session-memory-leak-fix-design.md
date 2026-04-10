# Claude Subprocess Memory Leak Fix — Session Lifecycle Management Design

## Background

Each Claude SDK subprocess occupies approximately 250MB of memory. The current `SessionManager` does not perform any cleanup for sessions in the `idle` state, causing subprocess to remain in memory permanently. In multi-session scenarios, memory accumulates continuously and eventually causes OOM.

### Root Cause

In `session_manager.py`'s `_finalize_turn()`:

```python
if final_status not in ("idle", "running"):
    self._schedule_session_cleanup(managed.session_id)
```

`idle` status (normal completion of a conversation round) is excluded from cleanup. `_schedule_session_cleanup()` also has a second skip for `idle` status internally. Result: the SDK subprocess for idle sessions is never released.

## Goals

1. Idle sessions automatically release SDK subprocess memory after a configurable timeout
2. Introduce a maximum concurrent session limit to prevent too many subprocesses being active simultaneously
3. Cleaned-up sessions restore transparently for users (DB records retained; `get_or_connect` rebuilds the connection when chatting again)
4. Timeout and concurrency limits are adjustable through the agent config page

## Design

### Three-Layer Defensive Architecture

```
┌─────────────────────────────────────────────────────┐
│  Layer 1: Unified delayed cleanup _schedule_cleanup  │
│  idle → configurable TTL (default 10 minutes)        │
│  completed/error/interrupted → short delay (30s)     │
│  cleanup task tracked in ManagedSession._cleanup_task│
│  expires → _disconnect_session → release memory      │
│  user sends again → get_or_connect transparent restore│
├─────────────────────────────────────────────────────┤
│  Layer 2: Concurrency limit + LRU eviction            │
│  active subprocesses ≤ max_concurrent (default 5)   │
│  new request arrives, if over limit → evict LRU non-running session│
│  all running → return 503 friendly message           │
├─────────────────────────────────────────────────────┤
│  Layer 3: Periodic inspection (safety net)            │
│  scans every 5 minutes, cleans timeout idle and terminal sessions│
│  prevents leaks from lost cleanup tasks               │
└─────────────────────────────────────────────────────┘
```

### Layer 1: Unified Delayed Cleanup `_schedule_cleanup()`

#### ManagedSession Fields

```python
idle_since: float | None = None                        # monotonic timestamp, recorded when entering idle
last_activity: float | None = None                     # updated each time a message is sent/received
_cleanup_task: asyncio.Task | None = None              # current cleanup timer (idle TTL or terminal short delay)
```

#### Trigger Points: `_finalize_turn()` and `_mark_session_terminal()`

All non-running statuses uniformly call `_schedule_cleanup()`:

```python
if final_status == "idle":
    managed.idle_since = time.monotonic()
if final_status != "running":
    self._schedule_cleanup(managed.session_id)
```

#### `_schedule_cleanup()` Unified Cleanup Logic

- **Cancel old timer**: Before scheduling, check `managed._cleanup_task`; if it exists and is not done, `cancel()` it before creating a new one
- **Delay determined by status**:
  - `idle` → configurable TTL (default 600 seconds = 10 minutes)
  - `completed/error/interrupted` → short delay `_TERMINAL_CLEANUP_DELAY` (30 seconds)
- After expiry, check: skip if session has resumed `running`; skip if `idle` and `idle_since` has been refreshed
- cleanup task tracked in `managed._cleanup_task`; `_disconnect_session()` will automatically cancel it

#### Recovery Path

Cleaned-up session DB records are retained (`AgentSession` rows are not deleted); when the user sends a message again, the existing `get_or_connect()` path → recreates `ClaudeSDKClient` → transparent recovery.

### Layer 2: Concurrency Limit + LRU Eviction

#### Checkpoint and Call Ordering

In `send_new_session()` and `get_or_connect()`, **`_ensure_capacity()` must be called before `client.connect()` and before the new session is added to `self.sessions`**. This ensures the new session is not counted in the active count.

#### Unified Cleanup Helper Method `_disconnect_session()`

All cleanup paths (TTL, LRU eviction, patrol) uniformly use this method to avoid omissions:

```python
async def _disconnect_session(self, session_id: str) -> None:
    """Safely disconnect and remove a session, handling consumer_task and connect_lock."""
    managed = self.sessions.get(session_id)
    if managed is None:
        return
    # Cancel idle cleanup timer
    if managed._cleanup_task and not managed._cleanup_task.done():
        managed._cleanup_task.cancel()
    # Cancel consumer_task (if still running) and wait for completion to prevent racing with disconnect
    if managed.consumer_task and not managed.consumer_task.done():
        managed.consumer_task.cancel()
        await asyncio.gather(managed.consumer_task, return_exceptions=True)
    managed.clear_buffer()
    try:
        await managed.client.disconnect()
    except Exception:
        logger.debug("disconnect non-fatal error for %s", session_id)
    self.sessions.pop(session_id, None)
    self._connect_locks.pop(session_id, None)
```

#### `_ensure_capacity()` Logic

```python
async def _ensure_capacity(self) -> None:
    """Ensure there is a free concurrency slot, evicting the least recently active non-running session if necessary."""
    max_concurrent = await self._get_max_concurrent()
    active = [s for s in self.sessions.values() if s.client is not None]

    if len(active) < max_concurrent:
        return

    # Evictable sessions: non-running status (idle / completed / error / interrupted)
    evictable = sorted(
        [s for s in active if s.status != "running"],
        key=lambda s: s.last_activity or 0
    )

    if evictable:
        victim = evictable[0]
        await self._disconnect_session(victim.session_id)
        return

    # All sessions are running → reject
    raise SessionCapacityError(
        f"There are currently {len(active)} active sessions, the maximum limit has been reached, please try again later"
    )
```

#### API Layer Error Handling

The router layer catches `SessionCapacityError` and returns:

```json
HTTP 503
{"detail": "There are currently {len(running)} active sessions, the maximum limit has been reached, please try again later"}
```

`SessionCapacityError` is defined as a custom exception in `server/agent_runtime/`.

### Layer 3: Periodic Patrol

A background `asyncio.Task` is created when `SessionManager` starts, covering both idle and terminal sessions:

```python
_PATROL_INTERVAL = 300  # 5 minutes, class constant

async def _patrol_once(self) -> None:
    """Single patrol: clean up all timed-out non-running sessions."""
    ttl = await self._get_idle_ttl()
    now = time.monotonic()
    for sid, managed in list(self.sessions.items()):
        if managed.status == "running":
            continue
        if managed.status == "idle" and managed.idle_since:
            if now - managed.idle_since > ttl:
                await self._disconnect_session(sid)
        elif managed.status in ("completed", "error", "interrupted"):
            activity_age = now - (managed.last_activity or 0)
            if activity_age > self._TERMINAL_CLEANUP_DELAY * 2:
                await self._disconnect_session(sid)
```

Cancel this task in `shutdown_gracefully()`.

### Configuration Reading

SessionManager adds two methods, following the existing `refresh_config()` pattern — each call creates a short-lived DB session + ConfigService to avoid holding stale long-lived connections:

```python
async def _get_idle_ttl(self) -> int:
    """Return idle TTL in seconds, default 600."""
    async with async_session_factory() as session:
        svc = ConfigService(session)
        val = await svc.get_setting("agent_session_idle_ttl_minutes", "10")
    return int(val) * 60

async def _get_max_concurrent(self) -> int:
    """Return maximum concurrent session count, default 5."""
    async with async_session_factory() as session:
        svc = ConfigService(session)
        val = await svc.get_setting("agent_max_concurrent_sessions", "5")
    return int(val)
```

**Note**:
- Do not store a `ConfigService` instance attribute in `SessionManager.__init__()`, because `ConfigService` depends on a request-scoped `AsyncSession`; holding it long-term causes the session to expire.
- `_ensure_capacity()` evicts only one idle session at a time. If an admin dynamically lowers `max_concurrent` (e.g., 10 → 3), the excess sessions are not immediately all cleaned up; instead they are evicted one by one by subsequent requests, with TTL/patrol as a fallback. This is an intentional gradual cleanup strategy.

### Backend Configuration API Extension

#### `SystemConfigPatchRequest` New Fields

```python
agent_session_idle_ttl_minutes: Optional[int] = None   # range 1-60
agent_max_concurrent_sessions: Optional[int] = None     # range 1-20
```

#### PATCH Handling

- Range validation: `1 ≤ idle_ttl ≤ 60`, `1 ≤ max_concurrent ≤ 20`, returns 422 if out of range
- Stored as strings in the `SystemSetting` table
- No need to map to environment variables (SessionManager reads directly through ConfigService)

#### GET Response

Add these two fields; values are read from `ConfigService.get_setting()`; returns default values (10 and 5) when no value is set.

### Frontend UI

#### Type Extension

`SystemConfigSettings` and `SystemConfigPatch` each add:

```typescript
agent_session_idle_ttl_minutes: number;
agent_max_concurrent_sessions: number;
```

#### AgentConfigTab UI

After the existing "Model Config" section, add a "Advanced Settings" block that is collapsed by default:

```
┌─ Agent Configuration ──────────────────────────────┐
│  [API Credentials]  Anthropic API Key / Base URL │
│  [Model Config]  Default model + advanced routing (collapsed) │
│                                                   │
│  ▶ Advanced Settings                              │  ← collapsed by default
│  ┌───────────────────────────────────────────┐    │
│  │  Session idle timeout (minutes)  [  10  ]   │    │
│  │  Resources released automatically after idle  │    │
│  │  time; restored automatically on next chat   │    │
│  │                                           │    │
│  │  Max concurrent sessions     [   5  ]        │    │
│  │  Max active agent sessions limit; excess     │    │
│  │  sessions auto-released (cleaned sessions    │    │
│  │  are persisted and restored on next chat)    │    │
│  └───────────────────────────────────────────┘    │
│                                                   │
│  [Save]                                           │
└───────────────────────────────────────────────────┘
```

- Input `type="number"` with `min`/`max` constraints
- Shares the same "Save" button and `isDirty` check with existing fields
- Do not add missing item checks in `config-status-store` (has default values, not required)

## Affected Files

| File | Change |
|------|------|
| `server/agent_runtime/session_manager.py` | Core: idle TTL, LRU eviction, patrol loop |
| `server/agent_runtime/service.py` | Propagate SessionCapacityError, no ConfigService injection needed |
| `server/routers/system_config.py` | Add PATCH/GET for two new config fields |
| `server/routers/assistant.py` | Catch SessionCapacityError → 503 |
| `server/routers/agent_chat.py` | Catch SessionCapacityError → 503 |
| `frontend/src/types/system.ts` | Add type fields |
| `frontend/src/components/pages/AgentConfigTab.tsx` | Advanced settings collapsible panel |

## Unchanged Parts

- `AgentSession` DB model unchanged (no new columns or migrations needed)
- `SessionRepository` unchanged
- `_schedule_idle_cleanup()` and `_schedule_session_cleanup()` have been merged into the unified `_schedule_cleanup()`
- Frontend session list and conversation UI unchanged (cleanup is transparent to users)
