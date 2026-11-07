---
status: accepted
---

# SessionActor serializes all ClaudeSDKClient calls in one task per session; CLI owns scheduling

ClaudeSDKClient cannot safely accept concurrent calls, and its anyio internals can deadlock under cross-task locking or violate internal state-machine assumptions. Give each Agent session one dedicated actor task exclusively owning its client: query/interrupt/queued-message withdrawal/disconnect enter a command queue and execute serially there. Read the message stream continuously throughout the session, interleaving with commands through `asyncio.wait` so interruption does not wait for an entire turn. When the message stream ends (CLI exits), the actor exits and session handling finalizes its terminal state. Publish messages only through `on_message`. Reject caller-owned locks and direct concurrent calls.

CLI is the sole message scheduler. Its queue merges messages received during turns at tool boundaries, or combines queued messages into a new turn afterward. Background completion notifications use the same queue and can start autonomous turns without query. Therefore:

- **Send every user message to CLI immediately**, never withholding because a turn is running. Each carries a server-assigned UUID; Send now uses `now` priority to interrupt the current turn and precede others.
- **The actor does not pair query and result**: result means one turn ended, not a particular query.
- **Session state follows CLI session state**: delivery means running; leave running only on CLI `idle` (after queue drain and background waiting) or actor exit. Result does not change state.
- **CLI `command_lifecycle` frames determine queued-message fate** (`started` admits, `cancelled` discards). Python SDK parsing drops those frames, so the actor reads raw `client._query.receive_messages()`, handles `command_lifecycle` itself, and sends other frames to SDK `parse_message`.

## Considered Options

- **Another ArcReel scheduling layer above CLI** (one user message per turn/result; reject messages during turns; query/result-based state and echo-registration clearing): CLI does not guarantee one-to-one correspondence. Merged messages and intervening autonomous turns misattribute results, clear echoes early and duplicate DB entries, or overwrite state. Mismatched schedulers require patching races individually.

## Consequences

- Every new SDK operation goes through the actor command queue, never directly through clients from external tasks.
- The actor is the resident in-memory session subject. SessionManager manages eviction, sweeps, and resumption (see `docs/adr/0029`).
- The actor depends on three private SDK entry points: `client._query.receive_messages()`, `parse_message`, and `_query._send_control_request` (for the unexposed queued-message withdrawal `cancel_async_message`). Upgrade tests lock all three.
