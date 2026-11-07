---
status: accepted
---

# Cancellation is available only for queued tasks; running tasks always reach terminal states

Generation-task cancellation is available only for `queued`. Once a worker claims a task as `running`, it cannot be cancelled: issued provider calls complete normally and their results become artifacts. Cancellation requests for running tasks are always rejected without changing task status, consistent with "Cancellation" in the glossary (`GLOSSARY.md`).

The rationale is the cost model. ArcReel media generation is billed by **provider API call count**: once issued, the provider continues generation and charging. Interrupting the local connection loses only the result, saving no cost; users pay for the call but receive no artifact. Queued tasks have made no calls, so cancelling them actually saves money. After running tasks finish, artifacts remain in version history; dissatisfied users can switch back or regenerate.

## Running-task cancellation was implemented, then withdrawn

The earlier implementation allowed running-task cancellation: the cancel API wrote intermediate state `cancelling`, called `cancel()` on the running coroutine through the worker's in-memory "task id → asyncio.Task" mapping, and worker cleanup advanced `cancelling` to `cancelled`. Three supporting mechanisms grew around this path:

- **Intermediate `cancelling` state**: task state, batch-member state/statistics, and partial deduplication indexes all gained another active state.
- **"0 rows means cancelled" protocol**: `mark_task_succeeded` / `mark_task_failed` guarded writes with `WHERE status='running'`; once tasks became `cancelling`, writes affected zero rows and worker exits called `mark_task_cancelled` instead as fallback.
- **Cancellation receipts**: artifacts could be partly or fully written when coroutines were cancelled, so "receipts" propagated from artifact registration and final writes through task-execution entry points, allowing written artifacts to be rolled back when terminal state became `cancelled`.

Reasons for withdrawal:

- **It contradicts the cost model.** Cancelling running tasks saves no issued-call costs while rolling back artifacts users already paid for.
- **Maintenance cost is disproportionate.** Receipts must propagate through every artifact-write path. Any new path omitting one leaves partial artifacts after cancellation; every new terminal exit must also remember the zero-row protocol. These constraints rely on caller discipline and are difficult to exhaustively test.
- **The requirement itself is narrow.** Users usually want to stop tasks not yet started; cancel-all already clears only queued tasks.

## Consequences

- The state machine is `queued → running → succeeded | failed`, plus `queued → cancelled` (user cancellation, `cancelled_by='user'`; queued downstream dependents are also cancelled with `cancelled_by='cascade'`). Only `queued` and `running` are active; the partial deduplication index's WHERE uses the same two values. Upgrade migration converges leftover `cancelling` rows to `cancelled`, cancelling their queued downstream tasks with `cascade` too.
- Cancellation entry-point behavior:
  - Single-task cancellation and cancellation preview reject running tasks with 409 and trilingual error copy.
  - Project-wide cancel-all selects only `queued`; running tasks are unaffected and uncounted. `skipped_running_count` counts only queued tasks claimed by workers during the request and therefore not cancelled.
  - Batch cancellation (creative Agent tools and remote MCP) cancels still-queued members; running members complete normally and count toward `skipped_running`.
  - The frontend shows cancel buttons only for queued tasks.
- Cascading cancellation concerns only queued downstream tasks: downstream tasks cannot be claimed before upstream completion, so a queued task's downstream tasks must also be queued when it is cancelled.
- Service shutdown does not cancel running tasks: `worker.stop()` waits for them to finish before exiting the main loop.
- **Retain the process-interruption fallback.** If a running coroutine receives `CancelledError` for process-level reasons (event-loop teardown, etc.), the worker calls `mark_task_interrupted` with `asyncio.shield`, moving directly from `running` to `cancelled`. This is not user cancellation; it prevents tasks remaining `running` and being treated as orphans again at the next restart (see `docs/adr/0007`). Its current cancellation source is `user`, inconsistent with the glossary's "internal system reclamation is not cancellation"; whether to introduce a separate source is a separate decision.
- Terminal writes still guard with `WHERE status=...` to prevent unauthorized transitions, but zero affected rows only produce logs, no longer reclassifying the task as cancelled.
- Failure to emit a success event does not roll back artifacts: written artifacts remain valid and exceptions propagate normally. Compensation on commit failure (not cancellation) remains.
- Running-task timeouts remain each video backend's responsibility through its own `max_wait` in `poll_with_retry`; timeouts raise `TimeoutError` and mark tasks `failed`. Workers add no outer timeout.
- Do not reopen running-task cancellation to "let users stop expensive generation." If that becomes a real requirement, first resolve provider-side cancellation and refund semantics instead of interrupting local connections.
