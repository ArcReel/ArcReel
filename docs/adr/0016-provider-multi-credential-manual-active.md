---
status: accepted
---

# Separate provider credential tables with manual active switching, without automatic rotation

Credentials are structured entities (name / secret / URL / active flag), with responsibilities distinct from shared KV settings. Support multiple credentials per provider in a dedicated `provider_credential` table, separate from shared configuration. Each provider has at most one `is_active`, backed by a DB partial unique index; users switch it manually in the UI with global effect. Deleting the active credential automatically selects the earliest `created_at` among the remaining credentials. Explicitly exclude automatic rotation, load balancing, encrypted storage, and usage statistics: KV-prefix naming is fragile and concurrent read-modify-write of one JSON field is complex, whereas a dedicated table has clear responsibilities and predictable behavior.

## Consequences

- The application must enforce active exclusivity within transactions, and switching/deletion must invalidate backend caches.
- Agent Anthropic credentials use the same multiple-credentials/single-active model but a separate table (see `docs/adr/0017`). Their deletion is stricter: an active credential cannot be deleted directly; switch first. There is no automatic earliest-credential selection.
