---
status: accepted
---

# Fetch mutable frontend provider configuration when consumed, without persistent caching

The module-level caches in `provider-models.ts` (`_cache` for built-in providers / `_customCache` for custom providers) never expire: `invalidateProviderModelsCache` has no callers. This causes the problem where "10s was added to a model in provider settings, but the project-settings duration picker still shows only the old values": the frontend cache has become a second source of truth that can drift, violating ADR 0018 (`supported_durations` has a single per-model source of truth) and ADR 0013 (capabilities have a model-level source of truth). Both fetchers will therefore become entirely stateless: every call directly fetches `GET /custom-providers` or `GET /providers`, and all module caches, in-flight promises, and invalidation functions are removed. "Fetch again when consumed" structurally eliminates this category of stale data instead of relying on callers to remember invalidation.

## Consequences

- Opening settings, the creation wizard, or the canvas each adds one provider-list fetch (a small JSON response). There is no thundering herd: each consumer fetches once at the page's top level and passes the result down through props; sibling components do not concurrently call the same fetcher, so in-flight deduplication is not introduced.
- The zustand store proposal is rejected: its benefit of "reactive consistency on the same screen" is unused under this application's route structure (settings and project pages are separate screens), making it overengineering. A store becomes appropriate only if a hot path with N concurrent consumers actually appears.
- `config-status-store` uses a separate fetch and its own `refresh()` (already called after changes). It produces only configuration warning dots and `availableMediaTypes`, without exposing model lists to duration/capability pickers, so it is outside this constraint.
- If a saved `default_duration` becomes out of range because a model's supported durations shrink, the backend resolver fails loudly as the fallback (ADR 0018). A frontend picker warning that a saved value is invalid is a separate issue, outside this change's scope.
