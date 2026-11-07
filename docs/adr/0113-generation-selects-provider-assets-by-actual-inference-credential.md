---
status: accepted
---

# Select provider assets with actual inference credentials; unavailable applicable links fail without fallback

Before reference-video submission, orchestration chooses image bytes or `asset://` per reference. Scope comes from backend-reported inference credentials: built-in Ark supplies active credential ID/API Key/Base URL, custom providers their own corresponding values, verified by `resolve_bound_scope` against binding digests. Do not re-infer credentials by names/domains/configuration. Config-only keys without credential records report none and act unbound. Match actual sent bytes to selected-version digests or treat links as inapplicable. Uploaded/imported links share selection rules.

Backend slot declarations (`provider_asset_roles`) determine acceptance. Official contracts support IDs only in Seedance 2.0/2.5 reference-image slots. Separate endpoints `ark-seedance-2`/`ark-seedance-2-5` declare this, `ark-seedance-1` does not; built-in/custom entries share endpoint declarations, not model-name inference. Users explicitly choose endpoints (name-prefilled but editable) and confirm account bindings before relays receive `asset://`; both steps are intentional even if a relay misadvertises support. First/end frames, Seedance 1.x, and Seedream send bytes. Add slots only with official contracts, without new routes/frames. Replacement preserves counts/order/prompt numbering; replaced slots are neither read/compressed nor counted in body budgets.

Selected pending (waiting/submitting/processing/uncertain) or failed jobs/links are applicable but unavailable. Fail before execution checkpoints/provider submission and clean staged media. Do not fall back to bytes: users often upload because bytes were rejected, so fallback risks paid/review failures while hiding nonuse. Missing-ID links are equally unavailable, not absent.

For provider image-review rejection, report provider code and suggest sheet upload, without automatic upload/retry/provider switching. Ark lacks image indices: name a reference only when exactly one exists with no first/end frame; otherwise do not guess.

## Consequences

Checkpoint schema v5 adds `provider_asset_references`, mapping staged images to content versions/asset references. Include it in request digests/version metadata, not currency basis. Identical content sent by bytes or references has identical free-reuse/currency results. Resume v4/earlier without asset references.

Shared acceptance/batch-preview/pricing projections check links too: reads assemble current-config backends, execution uses actual backends, both selecting identically. Unavailable links report `provider_asset_link_processing`/`provider_asset_link_failed` with matching parameters and no unit quote. After staging, recheck sent bytes; links becoming unavailable before submission fail before checkpoints. Checks add blockers only, without changing projected request facts or currency basis. Rate-limit routing needs only buckets and skips checks. Scope/acceptance: [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559).
