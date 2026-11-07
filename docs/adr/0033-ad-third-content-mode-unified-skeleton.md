---
status: accepted
---

# Ads/short films are the third content mode: derive the skeleton from the generation route

> The current architecture unifies reference routes for all three content modes with self-contained `video_units[]`; see
> [ADR 0045](0045-script-skeleton-registry-dual-resolvers.md) and
> [ADR 0055](0055-generation-route-locked-at-creation.md) for the current skeleton/route contract. The original
> `shots + reference_units` derived grouping explains only historical data and is no longer a runtime contract.

Ad/short-film mode (primarily commerce short videos) produces one video rather than a multi-episode series and must enter the type system. Introduce `ad` as the third content_mode value; the generation route remains an independent dimension, with project-level `generation_mode` determining whether the script uses a storyboard or reference-video skeleton.

## Decisions

- **`ad` is the third content_mode value**: reuse every content_mode-dispatched mechanism (profile variant `CLAUDE.ad.md`, SCRIPT_SHAPES, immutability after creation, and per-episode statistics dispatch in project summaries).
- **Derive the skeleton from the generation route**: the storyboard path uses flat `shots[]` (`shot_id`, E1S{n}), each shot carrying `section` and first-class voiceover copy `voiceover_text`; the reference_video path uses self-contained `video_units[]`, each unit owning its body, orchestrated duration, references, and artifact attribution. Runtime neither reads nor writes back old `reference_units`.
- **Ad exposes only storyboard and reference_video**: grid is unavailable because individual grid-cell resolution conflicts with product-fidelity goals; product/style references carry its visual-consistency value in ad mode.
- **Always one episode**: ad projects always have episodes `[{episode: 1, …}]`, with script `scripts/episode_1.json`. Episode mechanics (status/archive/versions/costs/export) require no structural changes, while the frontend hides episode semantics for ad. Future "multiple variants of one product" can extend this as one episode = one variant.
- **Inject shot-duration constraints dynamically by generation_mode**: the storyboard path strictly enumerates supported_durations (a model-capability constraint); reference-path units hold orchestrated durations of 1–300 seconds, projected into provider request tiers during generation preflight.

## Historical decision and reason for replacement

Initially, both ad routes used `shots[]` as content truth, persisting reference_video groups as a `reference_units` index referencing only `shot_id`. That shape required runtime hydration of two structures and split the reference route from narration/drama's self-contained-unit contract. The current design uniformly uses `video_units[]` for reference routes in every content mode; project-schema migration reads old `shots + reference_units` only once, and business code consumes only the new skeleton afterward.

## Consequences

- Expand VALID_CONTENT_MODES, SCRIPT_SHAPES, profile manifests, data validators, and the creation wizard for the third value; see the proposal and ADR 0034 for ad-specific fields (`target_duration`, `brief`, `products` bucket).
- Episode-ledger redesign must treat ad as an always-single-entry ledger/exempt it from splitting plans, without binary assumptions about content_mode.
- Startup migration preserves provable unit identities, boundaries, and historical artifacts in existing ad reference scripts; afterward, retain neither dual reads of old structures nor an entry point that re-derives them.
