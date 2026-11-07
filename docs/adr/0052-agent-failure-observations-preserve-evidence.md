---
status: accepted
---

# Agent failure observations preserve evidence without inferring root causes

> Presentation of the failure card's "default layer" in this ADR is amended by "Change the default layer to a one-sentence conclusion (revision, 2026-10-04)" below. The rest remains valid.

Agent startup errors reported by users can lose their exception type when the message is empty; SDK-synthesized turn errors have also been rendered as Agent answers because structured markers were lost at the event-log write boundary. Startup and turn failures will share the failure-observation contract "minimal generic envelope + redacted raw payload": the envelope carries only stages, times, project, and session context known to ArcReel; the raw payload preserves actually obtained exception chains, stderr, SDK messages / results, and upstream responses. Do not create an upstream-error-type enum or elevate upstream explanations into ArcReel root-cause judgments.

At the session-event-log write boundary, turn failures become system-failure events rather than Agent messages. Startup failures return immediately as structured API errors; both join existing diagnostic logs. Turn failures retain session-event-log persistence; startup failures do not persist across refreshes. Add no failure table, dedicated file, or historical-query endpoint, and do not proactively migrate or rewrite already materialized historical error events.

Raw payloads cover only failure objects actually received or generated; do not proactively copy prompts, normal streamed output, or full transcripts, while retaining context carried by the payload itself. Redact only secret values directly usable for impersonation or billing, such as API keys, authentication tokens, Cookies, passwords, and sensitive signed-URL parameters. Do not truncate, translate, or polish other paths, exception types, cause chains, stacks, or unknown fields.

The interface shares two-layer failure cards: the default layer shows the stage, observed type / status, and source-attributed raw message, explicitly stating these facts do not establish root cause; the expanded layer displays the complete redacted payload and allows copying. Offer only root-cause-independent actions: copy details, open Agent settings, and retry startup failures. Diagnostic logs remain downloadable through existing system settings, without duplicating that entry in failure cards. Do not automatically replay original messages for turn failures, avoiding duplicate execution of tool actions already performed.

## Explicitly rejected

- Classifying root causes from error text or generating targeted repair suggestions.
- Maintaining exhaustive mappings for unknown upstream errors or reducing them to an uninformative "unknown error." (Revised conclusion keys cover only values present in structured evidence; other cases fall back to stage-based generic keys, as described below.)
- Adding transcript-association backfills for low-value historical failed turns.
- Automatically replaying failed turns.

## Change the default layer to a one-sentence conclusion (revision, 2026-10-04)

Triggered by the Agent message-area section and item 9 of backend data adjustments in "Spec: Frontend refactoring and layout optimization based on shadcn/ui" (#2991). Replace the original sentence "the default layer shows the stage, observed type / status, and source-attributed raw message" with: the default layer shows a stage title, a one-sentence conclusion, and three actions: Agent settings, copy diagnostic information, and retry for startup failures. Move the observed type, status, source-attributed raw message, and complete redacted payload together into expandable, copyable "Details," keeping redaction rules unchanged.

Localize the one-sentence conclusion through the newly added `summary.key` in the failure-observation envelope. Derive keys only from structured evidence, mapping fields already supplied by the SDK and runtime rather than ArcReel root-cause judgments:

- For turn failures, use in order the SDK's `AssistantMessageError` value (except `unknown`), result `subtype` (`error_max_turns`, `error_max_budget_usd`), and upstream HTTP status (mapped to the same set of `AssistantMessageError` values).
- For startup failures, use exception classes along the explicit cause chain: `CLINotFoundError`, `CLIConnectionError`, `ProcessError`, `TimeoutError`.
- Without that evidence, fall back to `turn_failed` or `startup_failed`, truthfully concluding "an error occurred at runtime / during startup; see details for the original information."

Conclusion sentences describe observations only, without targeted repair suggestions or parsing error text. Historical failure events materialized before this revision have no key; show them with generic keys without migration. Decisions to preserve evidence, avoid inferring root causes from text, and avoid automatic replay remain unchanged.
