---
status: accepted
---

# Runtime predicates share a source with the actual execution path

Code determining provider execution behavior (billing basis, whether the finished video has audio, and validation admission) previously inferred it from intermediate ArcReel configuration fields, such as inferring whether a provider would produce audio from the configured resolution or quality tier. Providers, however, act only on fields in the request body. Normalization, fallback, truncation, and the layering of different bases intervene between configuration and request construction; inference can become inaccurate at each transformation, causing inconsistencies such as "audio requested, finished video silent, still billed at the audio-inclusive price." Decision: **all runtime predicates derive from fields actually sent to the provider and share their source with request construction**. To determine whether behavior occurs, read its actual input; when the same transformation is needed, call the same function instead of calculating it again at the predicate.

## Explicitly rejected

- **Inferring provider behavior from intermediate ArcReel fields**: transformations between intermediate and request fields (capability fallback, layering of billing rules, silent truncation) are invisible to the predicate, so edge cases inevitably diverge.
- **Copying request-construction logic into the predicate**: two implementations lack a shared source of truth, so changing one can leave the other behind; predicates should reuse construction-path outputs or the same transformation function.
- **Using billing-basis fields as request-basis predicates** (or vice versa): each basis layers different rules (such as provider exceptions that always bill for audio), so using one for the other makes predicates always true or always false for exceptional providers. Expose both bases side by side and consume each separately.

## Consequences

- Value paths for capability, billing, and validation decisions must be traceable to request-body fields or share the same transformation function with request construction. During review, challenge code that "reads configuration to infer execution behavior" under this principle.
- The declaration layer follows the same structure: the source of truth for video capabilities belongs to backend declarations by field (`VideoCapabilities`) and shares its source with request construction (see `docs/adr/0054`), corresponding to this principle on the static-declaration side. For declaration-layer sources of truth for image and other capability fields, see "Provider capabilities and contracts" in `.claude/rules/provider-capabilities.md`.
- Video duration constraints and pricing are evaluated using the resolution actually sent in the request; see `docs/adr/0086`.
