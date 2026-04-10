# Design Document: Assistant Session SSE Streaming

## 1. Background

The current assistant session pipeline already supports:
- Session and message persistence (`projects/.agent_sessions.db`)
- Claude Agent SDK integration
- Frontend session workspace (`assistant.html`)

However, message responses are still returned all-at-once after completion, causing:
- High time-to-first-token, long user wait times
- Tool call process is invisible (must wait for final result)
- Frontend cannot implement character-by-character rendering or progress feedback

---

## 2. Goals and Non-Goals

### 2.1 Goals

- Implement streaming assistant responses using `SSE (text/event-stream)`
- Frontend can incrementally render tokens and display status events (start, tool call, complete, error)
- Maintain existing session storage structure with minimal changes
- Retain synchronous interface as a fallback path

### 2.2 Non-Goals

- No WebSocket introduced in this phase
- No refactoring of existing skill execution logic in this phase
- No complex task queue systems (e.g., Celery) introduced in this phase

---

## 3. Approach Selection

For the "frontend-backend separation + one-way generation stream" scenario, SSE is preferred:
- Simple protocol, browsers natively support `EventSource`
- Low server-side migration cost, easy to integrate with FastAPI
- Semantics match "user sends one request, server continuously outputs"

Trade-offs:
- SSE is one-way; not suitable for complex bidirectional control (re-evaluate WebSocket later if "real-time interruption + interactive tool input" is needed)

---

## 4. Overall Architecture

```text
POST /messages                     GET /streams/{request_id}
Frontend --------> Backend saves user message --------> Frontend EventSource subscription
                          |                            |
                          |---- Claude SDK async ---->|
                          |---- tool/status events --->|
                          |---- delta token events --->|
                          |---- done/error ----------->|
                          |---- save assistant message--|
```

Design principles:
- Decouple sending messages from consuming the stream (two steps)
- Save user message first, then open stream
- Assistant final message is persisted at `done` time (optional extension: per-chunk persistence)

---

## 5. API Design

## 5.1 Create Stream Request (maintain POST semantics)

`POST /api/v1/assistant/sessions/{session_id}/messages`

Request body:
```json
{
  "content": "Generate character settings for episode 1",
  "stream": true,
  "client_message_id": "uuid-optional"
}
```

Response:
```json
{
  "success": true,
  "session_id": "xxx",
  "request_id": "req_xxx",
  "stream_url": "/api/v1/assistant/sessions/xxx/streams/req_xxx",
  "user_message_id": 101
}
```

Notes:
- When `stream=true`, returns `stream_url` instead of the full assistant text directly
- `client_message_id` is used for client-side retry idempotency (optional)

## 5.2 Subscribe to Stream

`GET /api/v1/assistant/sessions/{session_id}/streams/{request_id}`

Response headers:
- `Content-Type: text/event-stream`
- `Cache-Control: no-cache`
- `Connection: keep-alive`
- `X-Accel-Buffering: no` (if behind a reverse proxy)

Event format (SSE):
```text
id: 1
event: ack
data: {"request_id":"req_xxx","session_id":"xxx"}

id: 2
event: delta
data: {"text":"Hello, "}

id: 3
event: tool_call
data: {"name":"Skill","detail":"generate-script"}

id: 4
event: tool_result
data: {"ok":true,"summary":"episode_1.json generated"}

id: 5
event: done
data: {"assistant_message_id":102,"usage":{"tokens":1234}}
```

Error event:
```text
event: error
data: {"code":"SDK_ERROR","message":"..."}
```

Heartbeat event (every 15-30 seconds):
```text
event: ping
data: {"ts":"2026-02-06T16:00:00Z"}
```

---

## 6. Event Model and State Machine

Recommended event types:
- `ack`: stream established
- `delta`: text increment
- `tool_call`: tool call started
- `tool_result`: tool call result summary
- `meta`: model/skill info (optional)
- `ping`: keepalive heartbeat
- `done`: completed and persisted
- `error`: failed and terminated

State machine:
- `created -> streaming -> done`
- `created -> streaming -> error`
- `created -> cancelled` (optional on client disconnect)

---

## 7. Backend Implementation (FastAPI)

## 7.1 File Changes (minimal)

New files:
- `webui/server/agent_runtime/streaming.py`
  - Stream request registration, queue management, event serialization

Modified files:
- `webui/server/agent_runtime/service.py`
  - Add `start_stream_request(...)`
  - Add `stream_events(...)` (async generator)
  - Map Claude SDK async messages to SSE events
- `webui/server/routers/assistant.py`
  - `POST /messages` adds `stream=true` branch
  - New `GET /streams/{request_id}`

## 7.2 Key Implementation Points

1. Producer-Consumer Model
- Producer: pulls Claude SDK `query()` async messages
- Consumer: SSE generator continuously `yield`s events
- Claude SDK must enable `include_partial_messages=true` and parse `stream_event`'s `text_delta` for true incremental output

2. Disconnect Handling
- Detect `request.is_disconnected()`
- Cancel SDK task and release request resources when client disconnects

3. Persistence Strategy
- Persist user message before starting stream
- Persist assistant message at end (`done`)
- On exception, optionally persist an error summary message (for debugging)

4. Ordering Guarantee
- Each event has a monotonically increasing `id`
- `done/error` must be the terminal event

5. Fallback Strategy
- When `stream=false` or client does not support SSE, use original synchronous interface

---

## 8. Frontend Implementation (current vanilla JS + Streamdown)

## 8.1 Send Flow

1. First `POST /messages` (with `stream=true`) to get `stream_url`
2. Subscribe with `new EventSource(stream_url)`
3. Handle events and render incrementally
4. Close connection and refresh message history on receiving `done/error`

## 8.2 UI Rendering Strategy

- `ack`: show "Generating..."
- `delta`: append to current assistant bubble, pass to Streamdown for incremental Markdown rendering
- `tool_call/tool_result`: show status line below bubble
- `done`: mark as complete, re-enable input box
- `error`: show error and allow retry

Streamdown integration recommendations:
- All assistant messages rendered uniformly through `Streamdown` component
- Enable incomplete Markdown parsing (`parseIncompleteMarkdown`) to avoid code block/list flickering during streaming
- If CDN component fails to load, frontend falls back to plain text rendering for availability

## 8.3 Slash Command Compatibility

- `/` suggestion logic unchanged
- Only replace "post-send response handling" with stream consumption

---

## 9. Security and Gateway Compatibility

1. Authentication
- If header-based auth is needed later, native `EventSource` does not support custom headers
- Options:
  - Short-term: `stream_url` uses a one-time short-lived token
  - Medium-term: switch to `fetch + event-stream parser` (still SSE protocol)

2. Reverse Proxy
- Nginx/gateway must disable buffering (otherwise "pseudo-streaming")
- Increase timeout configuration appropriately (read timeout, keepalive)

3. CORS
- Maintain the same cross-origin policy as the API for SSE routes

---

## 10. Monitoring and Observability

Recommended metrics:
- `assistant_stream_requests_total`
- `assistant_stream_time_to_first_delta_ms`
- `assistant_stream_duration_ms`
- `assistant_stream_error_total`
- `assistant_stream_disconnect_total`

Key log fields:
- `session_id`, `request_id`, `project_name`
- `event_count`, `delta_chars`, `tool_calls`
- `error_code`, `error_message`

---

## 11. Phased Implementation Plan

## Phase 1: Protocol and Backend Minimal Loop
- [ ] `POST /messages` supports `stream=true` returning `stream_url`
- [ ] New `GET /streams/{request_id}` outputs `ack/delta/done/error`
- [ ] Assistant text persisted to database at `done`

Acceptance criteria:
- Browser sees text output incrementally
- User and assistant messages complete in database

## Phase 2: Tool Events and UI Status Enhancement
- [ ] Add `tool_call/tool_result/ping`
- [ ] Frontend shows "Calling skill... / Complete"
- [ ] Error state allows retry

Acceptance criteria:
- User can perceive "model thinking + tool execution" process

## Phase 3: Stability and Recovery
- [ ] Disconnect cleanup tasks
- [ ] Idempotency (`client_message_id`)
- [ ] Metrics and logging improvements

Acceptance criteria:
- No significant resource leaks or duplicate messages under high-frequency sending or poor network disconnects

---

## 12. Definition of Done

- [ ] Time-to-first-token significantly reduced (observable)
- [ ] Streaming rendering stable throughout, no freezes
- [ ] `done/error` always closes the loop; frontend state recoverable
- [ ] Message persistence consistent with history replay
- [ ] Synchronous interface retained, toggle can revert

---

## 13. Risks and Mitigation

1. Proxy buffering causing non-real-time output
   Mitigation: explicitly disable proxy buffering, add heartbeat

2. Server-side tasks hanging after client disconnect
   Mitigation: detect disconnect and cancel upstream tasks, set timeout

3. Inconsistency between streaming output and persistence
   Mitigation: use `done` as the persistence commit point; write error summary on exception

4. EventSource authentication limitation
   Mitigation: short-term token; long-term switch to `fetch + SSE parser`

---

## 14. Recommended First-Pass Change List (file level)

- `webui/server/agent_runtime/service.py`: add streaming interface and event mapping
- `webui/server/agent_runtime/streaming.py`: SSE event encapsulation, request management
- `webui/server/routers/assistant.py`: add stream route and `stream=true` branch
- `webui/js/api.js`: add stream-related API methods
- `webui/js/assistant.js`: integrate EventSource incremental rendering + Streamdown
- `tests/`: add unit tests for streaming interface and event sequences
