---
status: accepted
---

# Usage records project provider calls; tasks have one-to-many calls

The usage page combines queued/running tasks and completed provider calls in one timeline. Intuitive shared-storage approaches create `api_calls` at enqueue or join tables into composite-key rows. Reject both: `api_calls` records only actual external requests, the smallest cost/usage unit, with nullable `task_id` linking served tasks and `purpose` recording origin. `tasks` owns intent and scheduling; usage records are a read model, not an entity. Tasks supply in-progress rows, calls terminal rows, and frontend combines them in one column while retaining each state's vocabulary/refresh mechanism.

Money is spent per request, not intent. Tasks may have multiple calls (real retries, multi-unit reference video); welding records to tasks asserts one-to-one and records queued cancellations that never called providers. Unified read rows need composite keys and in-progress exceptions, patching a write-side defect.

Consequences: add `task_id`, `purpose`, and `session_id` to `api_calls`, and forward `user_id` through text calls. `api_calls.task_id` replaces `tasks.payload_json.api_call_id`. Add `cancelled` call status so user cancellations do not count as failures.
