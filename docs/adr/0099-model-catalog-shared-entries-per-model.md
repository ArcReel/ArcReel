---
status: accepted
---

# Shared model-catalog entries, channel multipliers, and factory/sync/user layers

The catalog owns metadata/prices for built-ins, custom providers, and Agents. Entries identify maker/official model name and are shared across providers. Provider models hold only channel facts (endpoints, API names, overrides, entry bindings), never separate prices; multipliers express differences. Costs are reference values for creators, and relays commonly use multipliers. One price per model is more suitable than channel-by-channel precision.

## Rules

- Compose factory snapshots, official sync, and whole-entry user copies. With sync enabled choose newer official/factory data; editing disables sync and uses only the copy; reenable to discard copies. Offline catalogs use factory/user layers only.
- Provider models have no sync switches. Officially listed built-ins follow official data except enabled state. User-added built-ins/custom models are user-owned. If official data later lists the same API name, replace the user model with official data retaining enabled state without extra notification.
- Official data chooses built-in media defaults, not users. Global/project model defaults handle user choices; provider defaults matter only for automatic selection when those are absent. Custom defaults remain user-owned.
- Built-ins represent official channels; credential base_url is only transparent reverse proxies/gateways. Relays use custom providers.
- Users create absent entries without sync. Later official identities do not merge automatically; offer Switch to official data, whose confirmation discards copies, like restore official data.
- Factory/sync distribute both entries and built-in model lists. Fetch models only supplements by matching returned IDs to provider API names; unmatched items are unlisted/manual additions. Account/region lists do not prove unavailability. Without listing APIs, add IDs manually.
- Unify models only. Credential shapes/backend implementations remain provider code; capability ownership is in `docs/adr/0104`.
- Amphibious models retain two entries (`docs/adr/0038`), same maker/name differentiated by media type only where needed.
- Freeze costs at call completion against then-current catalog, never recompute. Catalogs need current selectable models, not historical prices.

## Considered Options

- **Per-channel entries like LiteLLM**: precise AI Studio/Vertex/relay prices, but repeated capabilities and difficult original-model mapping.
- **Full provider-model price tables beyond multipliers**: supports different structures but adds precedence complexity for rare dual-channel/different-structure use. Edit entries for now; add fields later if needed.
- **Built-ins as provider templates**: conceptually unified but needs extensible credential types, identity migration, and proprietary endpoints. Benefits here are entirely model-level; later provider work remains possible.
- **Provider-model sync switches**: permits corrections but loses future API-name/multiplier updates; most cases are relays incorrectly configured as built-ins.
- **Field-level user diffs**: untouched fields still update, making disabled sync hard to explain. Whole copies mean wholly user-owned entries.

## Consequences

- Built-in metadata/prices leave code; price changes need no app release.
