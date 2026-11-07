---
status: accepted
---

# Mirror SDK transcripts into DB with eager flush; evict/resume idle sessions without losing history

SDK jsonl transcripts are tied to a local filesystem and conflict with the single-async-ORM deployment model (`docs/adr/0020`), including multiple users/PostgreSQL. Implement a custom SessionStore mirroring entries into DB (`agent_session_entries` and session-summary tables), controlled by `ARCREEL_SDK_SESSION_STORE` (default `db`; `off` falls back to SDK jsonl). A startup hook migrates local historical jsonl once. Default eager flush writes every entry to preserve SDK-resume context after crashes. Transcripts serve only SDK resume; UI timelines read session event logs (see `docs/adr/0048`).

## Consequences

- Evict idle sessions from memory after a delay (`agent_session_cleanup_delay_seconds`, default 300 seconds, with periodic sweep fallback), closing actor/SDK subprocesses to bound memory. Never evict active sessions: closing subprocesses interrupts turns, kills in-flight subagents, and prevents later autonomous turns. Prefer refusing new sessions under capacity pressure; restart the timer after CLI reports idle. Session state is the criterion: sessions remain running until CLI `idle`, including live subagents or another turn owed after completion, matching SDK completion semantics. There is no fallback maximum: without CLI idle, retain the session until process restart. DB transcripts preserve history through eviction; access rebuilds the actor with SDK resume by sdk_session_id.
- Eager flush amplifies writes; SDK coalesces frames with slow stores. `off` is suitable only for single-machine development.
