---
status: accepted
---

# Message rewriting uses application-level prefix forks, without SDK-native fork

Message rewriting means "return to before a historical user message and resend rewritten content": fork from a mid-session point, discarding later content. Claude Agent SDK `fork_session` is close but insufficient: `up_to_message_id` truncates at specified messages, but `_build_fork_lines` removes all `isSidechain` entries and reassigns every message uuid, losing whole subagent timelines and shared identity between original/branch historical messages. `rewind_files` rewinds only files, explicitly not conversations (official wording: "It does not rewind the conversation itself"). Claude Code CLI `/rewind` can rewind conversations, but it internally truncates/replays its transcript at the application layer, without SDK exposure.

Implement **prefix forks** in ArcReel's application layer: copy pre-rewrite transcript prefixes (including subagent subpaths) from DB mirrors (`agent_session_entries`) under new session_id values, start new SessionActors with `resume=新id`, and use rewritten messages as first input. This matches CLI `/rewind`'s approach of operating on owned transcript storage; CLI truncates jsonl while we copy database rows.

Key constraints and risk containment:

- **Fork only at user-message boundaries**. Previous turns have fully finished, ensuring tool_use/tool_result pairing and sidechain integrity. Reject rewriting while unresolved question cards exist, avoiding dangling tool_use in preceding turns.
- **One service entry point**. Supplying assembled prefixes for SDK resume is unofficial; contain knowledge/risk here without exposing assembly details to callers.
- **Copy entry uuids unchanged**, without remapping. Unique indexes/materialized directories are scoped by session_id, preventing cross-session uuid collisions. Historical messages retain names in every branch, avoiding extra identity bookkeeping for alignment and branches of branches.

## Anchor identity

Frontend rewrite anchors are event-log entry uuids, but truncation targets transcript entries: two uuid domains. They coincide for most entries: lazy generation directly uses transcript uuids for user entries, and prefix copying preserves them, recursively maintaining identity across branches of branches. The sole exception is active paths: POST acceptance must supply identities before SDK uuid assignment, minting `user-<hex>` first, then pairing through echoes into `agent_session_user_message_links`.

Resolution therefore has two stages, centralized in `EventLogService.resolve_user_message_anchor`: prefer mappings; otherwise verify that the event-log uuid belongs to a mainline plain-user entry (no subtype or parent_tool_use_id), then use identity equivalence. Fallback does not verify transcript existence: active entries without mappings may return nonexistent transcript uuids, rejected by prefix-fork slicing. Similarly, transcripts write tool receipts as `type:"user"`; mixed-text entries cannot be distinguished from actual user messages by entry type, so prefix forks reject those carrying tool_result to prevent paired tool_use dangling at prefix ends. These validations have distinct responsibilities, benefiting both resolution paths.

## Explicitly rejected

- **SDK-native `fork_session` + prompt overrides** (the only official approach): even with `up_to_message_id`, sidechain loss and uuid remapping invalidate it, losing subagent history and cross-branch alignment. Full-history forks without truncation additionally keep rejected erroneous branches consuming context tokens and contaminating generation, reducing correction to appended instructions.
- **Truncating original sessions in place**: deleting later transcript/event rows and resuming the same session_id violates append-only event logs (`docs/adr/0048`), invalidates incremental frontend projections and SSE `Last-Event-ID` resumption, and requires separate discarded-branch backups. Prefix forks preserve whole original sessions as backups; new logs lazily rebuild from transcripts through existing `docs/adr/0048` replay mechanisms.
- **Waiting for upstream**: mid-session forks have long remained feature requests without schedules; correction is a real pain point with weaker models and cannot wait indefinitely.

## Publish through compensation, not transactions

Forks persist three things: new-session transcript prefixes, new-session metadata, and original-session superseded pointers. They span transcript mirrors and metadata stores, committed separately without shared transactions. Each store append commits, and enumeration can see transcript rows without metadata. Publication is therefore nonatomic; service-level compensation reverses failures by clearing pointers, deleting new transcripts (including subagent subpaths), and deleting metadata, each independently tolerant. Cleanup failures only log without hiding original errors. Transactionally grouping `create` and `mark_superseded` merely moves the window to transcripts without changing this property.

Accepted window: before rollback, new session rows may exist before pointers settle, but they are idle, without actors or inputs. Actual harm requires session-list polling at that instant combined with immediate user deletion of the newly visible session, matching existing single-user local-deployment concurrency tradeoffs. Guard pointers with conditional `WHERE superseded_by IS NULL`, preventing two forks from overwriting the same session; duplicate forks fail here and compensate the whole operation.

## Consequences

- Mark originals superseded, pointing to new sessions; filter them from lists while preserving full data and ordinary idle eviction. Deleting a branch redirects predecessors to its successor, preventing nonexistent pointers from hiding predecessors forever.
- `superseded_by` expresses replacement UX: one visible timeline per session, predecessors exit. Store lineage separately as immutable `fork_parent_session_id` + `fork_anchor_uuid` (transcript domain), written at branching: the former replacement pointer is a mutable presentation projection during deletion, while lineage is immutable fact. Future tree views only need relaxing single-child constraints and presentation work, without data migration.
- Rewriting does not revert file/project-data side effects. SDK `enable_file_checkpointing` + `rewind_files` may be later enhancements, but blind spots (untracked Bash writes, queue/DB project writes) allow at most partial rollback without changing this decision.
- Event-log/frontend-projection contracts remain unchanged: new sessions express all truncation semantics, retaining append-only, immutable prefixes, and SSE resumption.
