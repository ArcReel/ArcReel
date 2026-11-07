---
status: accepted
---

# Compress reference-upload copies using a conservative shared limit and reactive 413 handling

I2I / I2V / R2V scenarios often send multiple large asset images (character/scene/prop sheets, the previous storyboard image, reference images) to providers as references. Base64 embedding easily pushes the total request body beyond provider limits, causing failures. At the `media_generator` chokepoint (the sole convergence point for all generation), actively compress **reference-upload copies** and downgrade them during preflight, retaining reactive 413 handling as a fallback. Centralize compression in `lib/references/reference_compression.py`, handling only temporary copies deleted after sending.

## Decisions

- **Compress only reference-upload copies**: source asset files and generated outputs retain full quality (outputs receive no save-time compression). Thus this mechanism cannot cause "generated 4K but cannot obtain 4K": reference-image resolution does not determine output resolution.
- **Use one conservative shared limit instead of exact per-provider byte limits**: default total request ≤8MB / individual image ≤4MB (within all known documented limits of built-in providers), stored in ConfigService with per-provider overrides. **Reactively catch HTTP 413 → downgrade one step and retry** to handle "unknown/potentially changing limits" (such as intermediaries or missing/outdated documentation).
- **Uniform JPEG q92 + 4:4:4**: every provider accepts JPEG, requiring no format branches. q92/4:4:4 is the "visually lossless" tier, minimizing generational damage from "our compression followed by the provider's internal compression" while protecting edges on text-bearing sheets.
- **Primarily reduce resolution at baseline** (long edge ≤2048, conditional passthrough, processing only when over limit); downgrade steps likewise prioritize resolution, with a quality floor of q80. Research identifies resolution as the primary, low-risk lever; high-frequency artifacts introduced by quality reduction harm conditioning (especially for sensitive i2v first frames).
- **Distinguish by role**: multi-image reference arrays use the full baseline and downgrade ladder; a single first/last frame is re-encoded only when exceeding byte limits, **never resized**, protecting sensitive first frames and preserving Sora's exact input-image pixel matching.

## Why not exact per-provider byte limits

Hardcoding request-body limits provider by provider violates the principle "do not guess/hardcode external provider data" (most providers do not document an overall limit and limits change frequently; each intermediary differs and cannot be verified). The conservative shared limit is an ArcReel safety-policy constant (like a timeout default), claiming no provider's actual number; reactive 413 handling self-corrects. Cost: an occasionally oversized limit configuration adds one failed 413 call. However, 413 is rejected at ingress without billing, adding only latency; intermediaries actually serving images have generally already increased body limits, so it triggers very rarely.

## Consequences

- Every backend error path must leave 413 recognizable: `vidu`/`dashscope` currently wrap `httpx.HTTPStatusError` as `RuntimeError`, losing the status code, and must be standardized to preserve it.
- Remove/migrate the old R2V per-task compression (`_compress_references_to_tempfiles`) together with the never-raised, therefore dead-code `except RequestPayloadTooLargeError` block. The chokepoint owns compression uniformly, avoiding double compression.
- Sora's exact input-image pixel matching is an independent gap predating this requirement (the first frame is not padded/resized to a valid tier), outside this design's scope; this design guarantees it does not worsen it.
