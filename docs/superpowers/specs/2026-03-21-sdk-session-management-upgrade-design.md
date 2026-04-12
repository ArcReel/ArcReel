# SDK Session Management Upgrade Design

## Background

The project upgrades from claude-agent-sdk 0.1.48 to 0.1.50, leveraging newly added session management APIs to simplify the architecture:

- `list_sessions(directory)` — lists sessions by cwd, returns `SDKSessionInfo` (includes `summary` auto-title)
- `get_session_info(session_id)` — queries metadata for a single session
- `tag_session(session_id, tag)` — tags a session
- `rename_session(session_id, title)` — renames a session

### Current Issues

1. **Dual-ID business coupling**: The application-layer `id` (UUID hex) and SDK-layer `sdk_session_id` are separate, and frontend/API routes/in-memory caches all use app_id, requiring repeated ID mapping lookups
2. **Crude title management**: On creation, the first 30 characters of the user message are used as the title, with no auto-summary
3. **Empty session bug**: `create_session` creates a DB record first; if the SDK connection fails, it leaves behind a ghost record with `sdk_session_id=null`

## Design Goals

- The business layer uniformly uses the SDK session_id as the identifier (DB retains `id` as primary key but no longer exposes it to business logic)
- Replace manual truncation with SDK's `summary` for auto-naming
- Fundamentally eliminate empty session issues
- Remove write maintenance of the `title` field in DB

## Architecture Changes

### Session Lifecycle: From "Create First, Then Send" to "Send Creates"

**Current flow** (two sequential steps, with empty session risk):

```
POST /sessions(project_name, title) → DB creates record (app_id)
POST /send(app_id, message)         → SDK connects → extracts sdk_session_id from stream → DB updates
```

**New flow** (unified send endpoint, DB record created only after successful SDK response):

```
New session:
POST /sessions/send(project_name, message, session_id=null)
  → SDK connect + query + start consumer task
  → Wait for sdk_session_id to arrive from stream (asyncio.Event, 10s timeout)
  → Create DB record (id=auto, sdk_session_id=xxx) + tag_session
  → Response returns {session_id: sdk_session_id, status: "accepted"}
  → Frontend uses session_id to connect GET /sessions/{session_id}/stream (SSE)

Subsequent messages:
POST /sessions/send(project_name, message, session_id=xxx)
  → Find existing session, send message
  → Response returns {session_id, status: "accepted"}
```

### DB Model Changes

**`agent_sessions` table**:

| Field | Change | Description |
|-------|--------|-------------|
| `id` | **Demoted to internal primary key** | Retains auto-generated UUID, no longer exposed to API/frontend |
| `sdk_session_id` | **Promoted to business identifier** | Add UNIQUE + NOT NULL constraint; all API routes, frontend, in-memory caches uniformly use this field as session identifier |
| `title` | **Write removed** | No longer written; column temporarily retained to avoid migration, ignored on read |
| `project_name` | Unchanged | SDK `list_sessions` filters by cwd, but cwd ≠ project_name, DB still needs this field for mapping |
| `status` | Unchanged | SDK doesn't track application-layer status, DB must retain it |
| `created_at` | Unchanged | |
| `updated_at` | Unchanged | |

**Key principle**: `id` is used only internally in DB (primary key, index). All external interfaces (API route parameters, SSE events, frontend store, in-memory dict keys) uniformly use `sdk_session_id`. The `SessionMeta` model's externally exposed `id` field is actually filled with the `sdk_session_id` value.

### Title Source Refactoring

**Read path** (`list_sessions` API):

1. DB query: filter by `project_name`, get `[{sdk_session_id, status, created_at, ...}]`
2. SDK query: call `list_sessions(directory=project_cwd, include_worktrees=False)` once to get all sessions' `summary` (explicitly disable worktree cross-querying to avoid cross-project pollution when multiple projects share the same git repo root)
3. Merge: join by `session_id`, inject `summary` into the returned `SessionMeta.title`; sessions returned by SDK but not in DB are ignored (missing `project_name` and other DB metadata)
4. Records with no matching summary (SDK data cleaned up, etc.) fall back to empty string

**SDK `summary` three-level fallback** (SDK internal logic):

1. `custom_title` (set via `rename_session()`)
2. Claude's auto-generated conversation summary
3. `first_prompt` (first user message)

**Write path**: None. Title is fully managed by the SDK.

### Tag Labels

When `sdk_session_id` first arrives, call `tag_session(sdk_session_id, f"project:{project_name}")`.
Note: `tag_session` is synchronous file I/O, needs to be wrapped with `asyncio.to_thread()`.
Not used for querying currently; sets the stage for future native SDK filtering by tag.

## Detailed Change List

### Backend

#### Removed

- `POST /sessions` creation endpoint (`routers/assistant.py`)
- `POST /sessions/{session_id}/messages` send endpoint — merged with creation into `POST /sessions/send`
- `PATCH /sessions/{session_id}` rename endpoint (`routers/assistant.py`)
- `CreateSessionRequest`, `UpdateSessionRequest` models
- `AssistantService.create_session()`
- `AssistantService.update_session_title()`
- `SessionManager.create_session()`
- `SessionMetaStore.update_title()`
- `SessionRepository.update_title()`

#### Added/Modified

- **`send_message` endpoint**: unified as `POST /sessions/send`, accepts `project_name` + `content` + `images` + optional `session_id` (body parameter). Without `session_id` it's a new session: SDK connect + wait for sdk_session_id + create DB + send message; with `session_id` it sends to an existing session. Returns `{session_id, status}`
- **`SessionManager.send_new_session()`**: new session dedicated method. Flow: connect → query → start consumer task → await `asyncio.Event` (**10s timeout**) for sdk_session_id → create DB record → return sdk_session_id. During new session creation, ManagedSession is stored in `self.sessions` with a temporary UUID key; after receiving sdk_session_id, the key is replaced. On timeout or error: cancel consumer task → disconnect client → remove temporary key from `self.sessions` → **return HTTP error** (frontend rolls back optimistic update accordingly). Timing guarantee: consumer task is started before method returns, SDK stream events are buffered in `message_buffer` (max 100), SSE connection replays via replay_buffer after connecting
- **`SessionManager._maybe_update_sdk_session_id()`** → renamed to `_register_new_session()`:
  - Creates DB record when sdk_session_id first arrives (`id=auto_uuid, sdk_session_id=xxx`)
  - Calls `tag_session()` (via `asyncio.to_thread`)
  - Sets `asyncio.Event` to notify `send_message` to return
- **`AssistantService.list_sessions()`**: merges DB query + SDK `list_sessions()` to inject summary (SDK `list_sessions` is synchronous, needs `asyncio.to_thread` wrapper)
- **`SessionMetaStore`**: `get()`, `update_status()`, `delete()` changed to look up by `sdk_session_id` instead of `id`; remove `update_sdk_session_id()` (new flow creates directly with sdk_session_id)
- **`SessionRepository`**: `get()`, `update_status()`, `delete()` WHERE conditions changed from `AgentSession.id == x` to `AgentSession.sdk_session_id == x`
- **`_dict_to_session()`** (`session_store.py`): key mapping point — `id=d["sdk_session_id"]` causes the externally exposed `SessionMeta.id` to be filled with the sdk_session_id value

#### Business Identifier Switch (app_id → sdk_session_id)

The following locations switch session identifier from app `id` to `sdk_session_id`:

- `service.py`: all API methods' `session_id` parameter semantics change to sdk_session_id; `_resolve_sdk_session_id` removed (session_id is sdk_session_id, no reverse lookup needed); logic for distinguishing two ID types in `_build_status_event_payload` removed; `_with_session_metadata` simplified
- `session_manager.py`: `sessions` dict key changed to use sdk_session_id; `get_or_connect`, `send_message` and other methods' `session_id` parameter semantics changed
- `sdk_transcript_adapter.py`: `read_raw_messages` uses sdk_session_id directly (no longer needs to obtain it indirectly from meta)
- `models.py`: `SessionMeta` removes `sdk_session_id` field (external `id` is already filled with sdk_session_id value, mapped by `_dict_to_session`); `AssistantSnapshotV2.sdk_session_id` removed (unified with `session_id`)
- `routers/assistant.py`: `{session_id}` route parameter maps directly to sdk_session_id
- `routers/agent_chat.py`: `agent_chat` endpoint synchronized — removes `service.create_session()` call, new sessions use the same send-first path as `POST /sessions/send` (SDK connect + wait for sdk_session_id); `session_id` semantics unified as sdk_session_id; existing session lookup changed to use sdk_session_id

#### DB Migration

Alembic migration:

1. Delete ghost records with `sdk_session_id IS NULL`
2. Add UNIQUE + NOT NULL constraint to `sdk_session_id` column (using `batch_alter_table`, `render_as_batch=True` already configured in `alembic/env.py`)
3. ORM model `AgentSession.sdk_session_id` changed from `Optional[str]` to `str` (non-null)
4. `title` column retained but no longer written to (server_default is already empty string)

### Frontend

#### Removed

- `API.createAssistantSession()` call
- `API.sendAssistantMessage()` old signature — merged into unified `API.sendMessage()` (calls `POST /sessions/send`)
- Title truncation logic in `sendMessage` (`content.trim().slice(0, 30)`)

#### Modified

- **`sendMessage`**: unified call to `POST /sessions/send`. In draft mode, don't pass `session_id`, get `session_id` from response, update store then connect SSE; for existing sessions, pass `session_id`
- **`SessionMeta` type** (`types/assistant.ts`): remove `sdk_session_id` field (backend-returned `id` is already sdk_session_id)
- **`AssistantSnapshot` interface** (`types/assistant.ts`): remove `sdk_session_id` field
- **`AgentCopilot.tsx`**: `displayTitle` fallback chain unchanged (`title || formatTime(created_at)`), title quality automatically improves
- **Test files**: all tests referencing `sdk_session_id` need updating (`useAssistantSession.test.tsx`, `stores.test.ts`, `router.test.tsx`, `AgentCopilot.test.tsx` and backend tests)

### Error Handling

**Backend**:
- SDK connection failure: `send_message` directly throws exception returning HTTP 500, no DB residue (empty session issue naturally eliminated)
- sdk_session_id wait timeout: set **10 second** timeout, after timeout cancel consumer task, disconnect SDK connection, ensure no resource leaks, return HTTP 504
- Existing session send failure: return HTTP error code, frontend rolls back accordingly
- `list_sessions` SDK call failure: fall back to returning only DB data with empty title (frontend falls back to timestamp)

**Frontend**:
- `sendMessage`'s `catch` branch needs to correctly handle new session creation failure: remove optimistically inserted user message turn, restore draft mode, show error prompt
- Fix existing bug: current situation where SDK hasn't stored messages but frontend optimistically displays them is fundamentally resolved by send-first mode + error rollback

## Backward Compatibility

- After frontend's `POST /sessions` call is removed, older frontend will receive 404; this is a forced upgrade with no backward compatibility
- DB migration will delete `sdk_session_id=null` ghost records (i.e., empty sessions); this is expected behavior
- Frontend cache/localStorage entries referencing old app_id will become invalid (API-returned `id` is now sdk_session_id), users need to refresh the page

## Out of Scope

- User-initiated renaming (no frontend entry, not implemented for now)
- `AssistantMessage.usage` token tracking
- `RateLimitEvent` capture
- `AgentDefinition`'s `skills`/`memory`/`mcpServers` declarative configuration
- SDK summary DB caching (summary for completed sessions doesn't change, can be a future performance optimization)
