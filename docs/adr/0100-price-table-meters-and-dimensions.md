---
status: accepted
---

# Price tables use meters × dimensions, one currency without conversion, and explicit unpriced calls

> The unpriced rule is refined by “Meter categories and entirely unmatched usage (amendment, 2026-10-15)” below. Code-defined dimensions may also expand with code; other decisions remain valid.

Unify pricing shapes into one currency and meters with dimension-selected unit prices and optional per-request allowances. Compute multiplier × Σ max(usage − allowance, 0) × unit price(dimensions), without shape/media dispatch. Major-provider pricing fits this, while fixed forms must awkwardly combine seconds, reference images, and request base charges.

## Rules

- Request/context tiers and service tiers are dimensions, not separate mechanisms or multipliers. Context tiers use total input tokens.
- Code defines dimensions; catalogs cannot invent them. Values derive from actual provider fields (`docs/adr/0056`). Add new dimensions such as `reference_to_video` to the closed set with validation.
- Usage meters do not overlap: uncached input + cache reads + cache-write tiers equals total input. Callers normalize provider usage before reporting; calculators know no providers.
- One table/currency per entry, from the maker's main site, without FX conversion.
- Channel differences multiply provider/user-preference and provider-model factors. Official model multipliers follow data; user-owned ones are entered manually. Apply to whole costs, not each meter separately.
- Missing prices mean unpriced, without other-model/tier fallback.
- Zero multipliers produce zero without lookup, never unpriced.

## Considered Options

- **Five token/image/second/request/character shapes**: simpler forms but every hybrid needs calculator changes. Forms may generate generic tables from presets.
- **Multiple site tables per entry**: domestic/international prices are not FX equivalents, but provider-table selection is excessive for few users; LiteLLM/new-api have one too. International users may edit entries.
- **One currency with fixed snapshot FX**: introduces conversion errors and another rate-maintenance/synchronization problem.
- **Default/nearest price fallback**: plausible wrong amounts mislead more than explicit unpriced results.

## Consequences

- `api_calls` must distinguish unpriced from zero; usage and totals show these separately.

## Meter categories and entirely unmatched usage (amendment, 2026-10-15)

Missing-price-for-any-meter would make nearly every video unpriced: truthful backends report seconds/images/calls even for seconds-only entries. Backends must not know pricing, so classify meters:

- Glossary meters declare token or count categories; calculators read declarations without hardcoded lists.
- Token meters with positive usage but no price make the whole call unpriced. They partition one token total, so missing portions cannot count as zero.
- Count meters (seconds/images/calls/characters/video tokens) absent from tables are not charged by that maker and incur nothing.
- At settlement, no reported usage for any table meter means unpriced, not zero, such as missing usage responses. Estimates with missing quantities still estimate rather than becoming unpriced.

Never fall back to other models/tiers for missing prices.
