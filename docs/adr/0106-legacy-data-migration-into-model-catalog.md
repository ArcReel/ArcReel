---
status: accepted
---

> Known-renamed-model rewrites are narrowed: historical v16→v17/v17→v18 migrations freeze their maps unchanged. Runtime settings rewrites are superseded by lifecycle rules in `docs/adr/0103`; preserve references and surface retirement/successors through entries.

# Migrate legacy catalog data by discarding custom prices and retaining only nonentry/nonpreset capabilities

Custom models carried user prices/durations/output limits while built-in metadata/prices lived in `registry.py`. Do not copy everything unchanged: old prices cannot express meters/dimensions and inferred durations would permanently mask updated entries. Retain explicit user intent only where new structures represent it accurately; disclose price changes in release notes.

## Rules

- Discard custom prices, without inferred multipliers/user entries. Matching entries use catalog prices with provider/model factors one; unmatched models are unpriced (`docs/adr/0105`). Frozen history stays unchanged.
- Match legacy custom models to factory entries by `docs/adr/0105` for capability comparison only, without storing matches. Preserve endpoints without reinference.
- Preserve mapped explicit overrides except ComfyUI (`docs/adr/0082`). Convert durations/output limits only when differing from both entries and name presets; unmatched values become overrides when incomparable. Preserve preferred resolution.
- Unmatched provider models can override capabilities too: media comes from endpoints and uncovered dimensions show runtime fallbacks.
- Generate first data-repository version once from `registry.py`, correcting known pricing/retired entries. Code then holds no models/prices. Enable listed models by default except previously hidden ones.
- Known-renamed out-of-list settings/project references rewrite via retirement maps; retain others with explicit reselection errors, never silent fallback, subject to amendment above.

## Considered Options

- **Infer factors from old/official prices**: works only with matching currencies, flat official prices, and equal input/output ratios. Other cases were unrepresentable anyway; users can enter factors after discarding reference prices.
- **Create unmatched priced entries**: unknown makers force guessed identities and conflict with `docs/adr/0105`.
- **Convert all durations**: almost all legacy videos received inferred defaults, which would become permanent unrequested overrides suppressing sync.
- **Build user models from every dangling settings/project reference**: mostly deleted/bypassed-UI data with little benefit; silent fallback unexpectedly changes models/prices.

## Consequences

- Upgrades replace custom prices with entry prices or unpriced states. Release notes explain changes and multiplier-based relay differences.
