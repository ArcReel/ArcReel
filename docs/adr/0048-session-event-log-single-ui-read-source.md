---
status: accepted
---

# Session event log: the UI timeline's sole read source, separate from SDK transcript responsibilities

If Agent-conversation UI truth is spread across sources (SDK transcripts, memory buffers, streaming previews) without shared message identity, consistency can only be patched with content-comparison heuristics, continually leaking missing/duplicate messages and inconsistent rendering at boundaries. Add a **session event log** (an independent persistent table with monotonically increasing seq per session) as the UI timeline's sole read source:

- Entries are **typed at the write boundary**: semantic identification of task notifications, interruptions, skill invocations, subagent ownership, etc. happens and persists only when logging. Skill entries record names and arguments only; full injected text does not enter the log.
- **Assign user-message identity at send time; log it when accepted by the Agent**: POST assigns a message uuid server-side and passes it to the CLI. The message first becomes a **queued message**, server-memory state like drafts, cleaned up with the session and not logged, delivered through the POST response and entry stream. Only when CLI reports processing has begun (`command_lifecycle`'s `started`) is it logged and assigned seq, placing it at the moment it actually enters the conversation. CLI user-message replay associates transcript identity by uuid only, without creating entries. The frontend renders no locally synthesized messages. Retries use request-side idempotency keys (held in a memory table while queued and the log afterward). A new session's first message is an exception: it is written as seq 0 when CLI reports `init`, without queueing, retaining session-creation idempotency and the log fallback carrying the key at seq 0. New sessions have no earlier output, so write and acceptance positions coincide.
- **SSE pushes an entry stream** (the SSE event's `id` is seq, with cursor-based resumption after disconnect). The stream lifecycle follows the session, not turns: status changes push only `status` events without closing the stream. Turn assembly (merging consecutive assistant messages, backfilling tool_result, updating tasks in place) is a pure function in the frontend projection layer.
- **Streaming previews (drafts) are server-memory state**, identified by message `message_id` and precisely replaced at completion by log entries with the same `message_id`. They are not logged and are lost on crashes (consistent with agent memory).
- **Record all subagent messages** as entries with parent_tool_use_id. The frontend groups by parent into collapsible subtask cards, showing only cards in the main timeline.
- The log is a **materialized view** of transcripts: it can be rebuilt by transcript replay and is lazily generated on first access to old sessions. Repair drift through rebuilding, without bidirectional synchronization.
- **SDK transcripts are only for SDK resume** (agent memory); never mix in UI-only entries, which resume would feed back to the agent.

## Explicitly rejected

- **Consolidating deduplication logic without unifying message identity**: content-comparison boundary leaks cannot be eliminated.
- **Logging at POST time and placing messages at send time**: CLI defers messages received during a turn until a tool boundary or after the turn. Send-time positioning would place Agent output produced after sending but before acceptance after that message, making it read as a response to it and disagreeing with positions rebuilt from transcripts.
- **Treating CLI replay as acceptance time**: a turn's first message is replayed only just before the Agent's first output; messages sent to idle sessions would remain queued throughout startup and time-to-first-token.
- **Using transcripts directly as the UI log**: SDK-controlled write timing prevents assigning identity when users send; UI-only entries contaminate resume; separate subagent subpaths do not share UI temporal structure.
- **Dual old/new protocols behind a feature flag**: the frontend would maintain two store implementations in parallel.

## Consequences

- New UI event types must be typed at the write boundary. Renderers and readers neither infer semantics nor deduplicate; fix UI consistency by correcting write-boundary typing or rebuilding logs through replay.
- Coexistence of event-log and transcript tables is intentional dual writing: one is the UI read model, the other agent memory. Their content overlaps, but protocols and timing differ; merging tables would restore the coupling this ADR eliminates.
- Transcript eager flush (`docs/adr/0029`) serves only to retain context for SDK resume after crashes; event logs provide UI reconnection snapshots.
