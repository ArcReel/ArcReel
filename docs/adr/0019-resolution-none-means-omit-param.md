---
status: accepted
---

# Omit unset clarity-only parameters such as resolution instead of supplying our defaults

Keeping hardcoded per-model resolution defaults such as `DEFAULT_VIDEO_RESOLUTION` guesses each provider's defaults, scatters maintenance, and erases the distinction between unset and explicitly selected tiers. Remove these tables. Resolve resolution as a **clarity-only** parameter by project → legacy → custom-model default → None. None means omitting the parameter from the SDK call and using the SDK's own default, not an ArcReel fallback; return default ownership to the SDK.

## Consequences

- Do not add a system-wide default resolution; custom-provider model-level defaults already cover system-level semantics.
- **Boundary**: this decision covers only backends where resolution is orthogonal to aspect ratio and omittable. When size must **carry aspect ratio** (such as OpenAI Sora size or image size), `aspect_size` in `docs/adr/0011` always calculates and sends it, never omitting None. That path is opposite without conflict. Native-name normalization (OpenAI size / Grok resolution / Gemini image_size) is also centralized in `docs/adr/0011`, rather than static per-backend translation tables.
- Reference-video generation no longer has a fallback-resolution exception. Both video routes follow this decision consistently; see `docs/adr/0086`.
