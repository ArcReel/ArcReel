---
status: accepted
---

# Embed a multi-user data model and `_scope_query` seam in open source, without implementing tenancy

Adding NOT NULL foreign-key columns and backfilling live tables later costs much more than embedding them with `server_default` initially, and would fork open-source/commercial schemas. Add `user_id` (always `"default"`) to Task/ApiCall/ApiKey/AgentSession, create a users table (default admin), and provide a no-op `_scope_query` override point in the Repository base. The commercial version extends fields through migrations and overrides `_scope_query` in subclasses with user_id filtering for visibility isolation. Do not implement directory isolation, login flows, quotas, or an administration UI.

## Consequences

- Open source carries apparently useless no-ops and constant `"default"` values. **Do not remove these as dead code.**
- `claim_next` uses native SQL, which `_scope_query` cannot intercept; this is a known exception requiring a commercial override.
