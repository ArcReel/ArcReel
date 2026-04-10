# ClaudeSDKClient Migration Design

## Overview

Migrate the project from `query()` to `ClaudeSDKClient`, enabling native multi-turn conversation support, background continuous execution, reconnection after disconnect, and other features.

## Design Decisions

| Decision | Choice |
|----------|--------|
| Migration target | Fully migrate to native ClaudeSDKClient sessions |
| History queries | UI display + session resume |
| Message storage | SDK session_id + on-demand transcript reads |
| Connection lifecycle | Background continuous execution + frontend reconnect on disconnect |
| Reconnect mechanism | Attempt SSE reconnect immediately on page enter |
| Service restart | Graceful shutdown (wait for current turn to complete) |
| Message format | Frontend adapts directly to SDK message structure; no conversion layer |

---

## Architecture Design

### Overall Architecture

```
Current architecture:
┌─────────┐    ┌─────────┐    ┌─────────────┐
│ Frontend│───▶│ FastAPI │───▶│ query()     │ ← new session each time
└─────────┘    │ + SQLite│    │ + prompt    │
               └─────────┘    └─────────────┘

New architecture:
┌─────────┐    ┌─────────────────┐    ┌──────────────────┐
│ Frontend│───▶│ FastAPI         │───▶│ ClaudeSDKClient  │
└─────────┘    │ + SessionManager│    │ (background run) │
    ▲          └────────┬────────┘    └────────┬─────────┘
    │                   │                      │
    │ SSE reconnect     │ metadata storage     │ transcript
    └───────────────────┴──────────────────────┘
```

### Core Components

| Component | Responsibility |
|-----------|---------------|
| `SessionManager` | Manages all active ClaudeSDKClient instances, handles lifecycle |
| `SessionMetaStore` | SQLite storage for session metadata (id, title, project, status, timestamps) |
| `TranscriptReader` | Reads SDK transcript files, returns message list as-is |
| `StreamBridge` | Bridges ClaudeSDKClient message stream to SSE |

---

## Data Storage Design

### Unified Transcript Storage Path

```
projects/.agent_data/
├── transcripts/                    # Transcripts for all sessions
│   ├── {session_id}.json          # SDK-generated complete conversation record
│   └── ...
├── sessions.db                     # SQLite metadata
└── checkpoints/                    # Optional: checkpoint data for resume
```

### ClaudeAgentOptions Configuration

```python
options = ClaudeAgentOptions(
    cwd=project_path,
    extra_args={
        "--transcript-dir": str(AGENT_DATA_DIR / "transcripts")
    }
)
```

### SessionMetaStore Table Schema

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,              -- Our generated session_id (UUID)
    sdk_session_id TEXT,              -- SDK-returned session_id (for resume)
    project_name TEXT NOT NULL,
    title TEXT DEFAULT '',
    status TEXT DEFAULT 'running',    -- running | completed | error | interrupted
    transcript_path TEXT,             -- Relative path: transcripts/{id}.json
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_sessions_project ON sessions(project_name, updated_at DESC);
CREATE INDEX idx_sessions_status ON sessions(status);
```

### Docker Container Volume Mapping

```yaml
volumes:
  - ./data/agent_data:/app/projects/.agent_data
```

---

## SessionManager Design

### Data Structures

```python
class ManagedSession:
    client: ClaudeSDKClient          # SDK client instance
    sdk_session_id: str              # SDK-returned session_id
    status: Literal["running", "completed", "error", "interrupted"]
    message_buffer: list[Message]    # Cache of recent messages (for replay on reconnect)
    subscribers: set[asyncio.Queue]  # Currently subscribed SSE connections

class SessionManager:
    sessions: dict[str, ManagedSession]  # session_id -> ManagedSession

    async def create_session(project_name: str, options: ClaudeAgentOptions) -> str
    async def send_message(session_id: str, content: str) -> None
    async def subscribe(session_id: str) -> asyncio.Queue  # SSE subscription
    async def unsubscribe(session_id: str, queue: asyncio.Queue) -> None
    async def get_status(session_id: str) -> SessionStatus
    async def shutdown_gracefully() -> None  # Graceful shutdown
```

### Background Execution Mechanism

1. `send_message()` starts a background task to consume `client.receive_messages()`
2. Messages are simultaneously: pushed to all `subscribers`, cached to `message_buffer`
3. When frontend disconnects, it only calls `unsubscribe()`; the background task continues running
4. When frontend reconnects, it calls `subscribe()`, replays buffer messages first, then receives new messages

### Message Caching Strategy

- `message_buffer` retains the latest 100 messages
- Buffer is cleared after session completion (history is read from transcript)

---

## Message Format Design

### Backend Passes SDK Messages Through Directly

```python
class TranscriptReader:
    def read_messages(self, session_id: str) -> list[dict]:
        """Read transcript and return SDK message list as-is."""
        transcript_path = self.transcripts_dir / f"{session_id}.json"
        with open(transcript_path) as f:
            data = json.load(f)
        return data.get("messages", [])
```

### Frontend Adapts to SDK Message Types

| SDK Message Type | Key Fields |
|-----------------|-----------|
| `UserMessage` | `type: "user"`, `content: str \| ContentBlock[]` |
| `AssistantMessage` | `type: "assistant"`, `content: ContentBlock[]`, `model` |
| `SystemMessage` | `type: "system"`, `subtype`, `data` |
| `ResultMessage` | `type: "result"`, `subtype`, `duration_ms`, `total_cost_usd` |

### ContentBlock Types

| Type | Fields |
|------|--------|
| `TextBlock` | `type: "text"`, `text` |
| `ThinkingBlock` | `type: "thinking"`, `thinking`, `signature` |
| `ToolUseBlock` | `type: "tool_use"`, `id`, `name`, `input` |
| `ToolResultBlock` | `type: "tool_result"`, `tool_use_id`, `content`, `is_error` |

---

## API Interface Design

### REST API

| Endpoint | Method | Function |
|----------|--------|---------|
| `/api/v1/sessions` | POST | Create session |
| `/api/v1/sessions` | GET | List sessions (supports `project_name` filter) |
| `/api/v1/sessions/{id}` | GET | Get session details (including status) |
| `/api/v1/sessions/{id}` | PATCH | Update session (title) |
| `/api/v1/sessions/{id}` | DELETE | Delete session |
| `/api/v1/sessions/{id}/messages` | GET | Get message history (read from transcript) |
| `/api/v1/sessions/{id}/messages` | POST | Send message |
| `/api/v1/sessions/{id}/stream` | GET | SSE stream (subscribe to real-time messages + reconnect) |

### Key Interface Details

```python
# POST /api/v1/sessions
Request:  {"project_name": "my_project", "title": ""}
Response: {"id": "uuid", "status": "running", "created_at": "..."}

# GET /api/v1/sessions/{id}/stream
# SSE event stream behavior:
# 1. If status=running: replay buffer first, then stream live
# 2. If status=completed: return empty stream (history from /messages)
# 3. Event format: push SDK Message JSON directly

# POST /api/v1/sessions/{id}/messages
Request:  {"content": "user input"}
Response: {"status": "accepted"}  # Returns immediately; messages pushed via SSE
```

### Changes from Existing API

- Remove `/sessions/{id}/streams/{request_id}` — simplified to single `/stream` endpoint
- `/messages` GET reads from transcript instead of SQLite

---

## Frontend State Management Refactor

### Core State

```javascript
// Existing state (retained)
const [sessions, setSessions] = useState([]);
const [currentSessionId, setCurrentSessionId] = useState("");
const [messages, setMessages] = useState([]);        // Stores raw SDK messages
const [streamingMessage, setStreamingMessage] = useState(null);  // Current streaming message
const [input, setInput] = useState("");
const [sending, setSending] = useState(false);

// New state
const [sessionStatus, setSessionStatus] = useState("idle");  // idle | running | completed | error
```

### Session Entry Flow

```
1. Switch to session_id
      │
2. GET /sessions/{id} to get status
      │
      ├─ status=completed ──▶ GET /messages to load history
      │
      └─ status=running ──▶ Connect SSE /stream
                                  │
                           Receive messages, append to messages
                                  │
                           Receive ResultMessage ──▶ status=completed
```

### SSE Reconnect Logic

```javascript
const connectStream = useCallback((sessionId) => {
  const source = new EventSource(`/api/v1/sessions/${sessionId}/stream`);

  source.onmessage = (event) => {
    const message = JSON.parse(event.data);
    setMessages(prev => [...prev, message]);

    if (message.type === "result") {
      setSessionStatus("completed");
      source.close();
    }
  };

  source.onerror = () => {
    // Reconnect after 3 seconds on disconnect
    setTimeout(() => connectStream(sessionId), 3000);
  };
}, []);
```

---

## Graceful Shutdown and Session Resume

### Service Shutdown Flow

```python
# Register shutdown signal handler
@app.on_event("shutdown")
async def shutdown():
    await session_manager.shutdown_gracefully()

class SessionManager:
    async def shutdown_gracefully(self):
        for session_id, managed in self.sessions.items():
            if managed.status == "running":
                # 1. Wait for current turn to complete (max 30 seconds)
                try:
                    await asyncio.wait_for(
                        managed.current_turn_task,
                        timeout=30
                    )
                except asyncio.TimeoutError:
                    # 2. Interrupt on timeout
                    await managed.client.interrupt()

                # 3. Update status to interrupted
                managed.status = "interrupted"
                self.meta_store.update_status(session_id, "interrupted")

                # 4. Disconnect
                await managed.client.disconnect()
```

### Resume After Service Restart

```python
class SessionManager:
    async def resume_session(self, session_id: str) -> ManagedSession:
        meta = self.meta_store.get(session_id)

        # Resume session using the SDK's resume parameter
        client = ClaudeSDKClient(options=ClaudeAgentOptions(
            resume=meta.sdk_session_id,  # SDK session_id
            cwd=get_project_path(meta.project_name),
            # ... other configuration
        ))
        await client.connect()

        managed = ManagedSession(client=client, ...)
        self.sessions[session_id] = managed
        return managed
```

### Frontend Handling of `interrupted` Status

```javascript
// On entering session
if (session.status === "interrupted") {
  // Show prompt: "Session was interrupted. Continue?"
  // After user confirms, POST /messages to trigger resume
}
```

---

## File Change Checklist

### Backend (Python)

| File | Action | Notes |
|------|--------|-------|
| `webui/server/agent_runtime/session_manager.py` | Create | SessionManager + ManagedSession |
| `webui/server/agent_runtime/session_store.py` | Rewrite | Simplify to SessionMetaStore |
| `webui/server/agent_runtime/transcript_reader.py` | Create | Read SDK transcript |
| `webui/server/agent_runtime/service.py` | Rewrite | Use SessionManager |
| `webui/server/agent_runtime/models.py` | Simplify | Remove AgentMessage, retain only Session metadata |
| `webui/server/routers/assistant.py` | Rewrite | New API structure |

### Frontend (React)

| File | Action | Notes |
|------|--------|-------|
| `frontend/src/api.js` | Update | New API endpoints |
| `frontend/src/react/hooks/use-assistant-state.js` | Rewrite | New state management logic |
| `frontend/src/react/components/chat/ChatMessage.js` | Update | Adapt to SDK message format |

---

## Implementation Order

1. **Backend Core Components**
   - SessionMetaStore (SQLite metadata)
   - TranscriptReader (read transcript)
   - SessionManager (manage ClaudeSDKClient)

2. **Backend API**
   - New route structure
   - SSE streaming endpoint

3. **Frontend Adaptation**
   - Update API calls
   - Update message rendering components for SDK format
   - State management refactor

4. **End-to-End Testing**
   - Create session
   - Multi-turn conversation
   - Reconnect after disconnect
   - Session resume
