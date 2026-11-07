---
status: accepted
---

# Media dimensions: aspect_ratio alone determines ratio and always takes priority; resolution determines clarity only

Storyboard-image/video output dimensions (width×height) previously came from backend-specific static "(resolution tier, ratio) → pixels" tables or directly passing "resolution values" as dimensions. The problem is **dimensions carry ratios**: any ratio information in resolution values (incorrect hardcoded table entries or custom values such as `1920x1080`) can override project `aspect_ratio`, producing wrong-ratio images. Layer-by-layer investigation (8 image backends + 8 video backends + custom resolution values) confirmed a **mechanism defect**, not an isolated bug:

- OpenAI image `OPENAI_IMAGE_SIZE_MAP` incorrectly assigned 1K `3:4`/`4:3` the pixels of adjacent 9:16/16:9 ratios (correct orientation, wrong proportions by ±25~31%). I2I (`images.edit`) omitted size entirely, leaving ratios to upstream defaults (the user's observed wrong-ratio images came from this path).
- DashScope qwen registry `resolutions` are ratio-bearing pixel values (such as 16:9 `2688*1536`). With project 9:16, old `if explicit: return explicit` passed them unchanged, losing the ratio.
- OpenAI/NewAPI video tables contained dall-e-style 4:7 values and indivisible 1080, while unchanged unknown/custom values could be invalid.

Product principle: **`aspect_ratio` is the sole output-ratio source of truth and always takes priority. Resolution (preset tiers or custom values) determines only clarity scale, never ratio. Match ratios as exactly as backend dimension constraints allow; when resolution is missing but dimensions are required to control ratio, default to 720P. Wrong-ratio artifacts are unusable.**

## Considered Options

- **Fix each backend's static tables**: rejected. Errors are scattered; new ratios/backends still require copying tables, without fixing the root cause of ratio-bearing resolution values overriding aspect_ratio.
- **Unified dimension mechanism `lib/backends/aspect_size.py`**: accepted. Derive exact dimensions from ratio and short edge: legal dimensions = `(aw·round_to·t, ah·round_to·t)` (`aw:ah` is the reduced ratio), inherently without ratio deviation and divisible by `round_to`. Backends clamp to their pixel constraints (`max_long_edge` / `max_total_pixels`). Normalize resolution to "short edge": tiers use a lookup, custom `WxH` uses `min` to strip ratio, and `None` falls back to 720.

## Consequences

- **① Strip ratio from custom resolution values**: if users enter `1920x1080` (16:9) but projects require 9:16, take only `min=1080` as short edge and retain 9:16. Frontend `ResolutionPicker` still permits free input; backend normalization is the sole enforcement point.
- **② Replace backend static tables with the unified mechanism**: retire `OPENAI_IMAGE_SIZE_MAP` and DashScope `_SIZE_BY_RATIO`/`_WAN_PIXELS_BY_BUDGET`/`_EDIT_SIZE_BY_RATIO`. Backends accepting arbitrary pixels (gpt-image-2, DashScope qwen-image-2.0 fusion series and wan method-two pixels, qwen-edit-plus/max) have zero ratio deviation. OpenAI image I2I passes size symmetrically with T2I.
- **③ Decouple billing from output dimensions**: OpenAI image primary paths bill by token (the vast majority); remove `(resolution, aspect_ratio) → size` reverse lookup from billing fallbacks. **Cost**: when SDK usage is absent, fallback costs can no longer distinguish dimensions (using default `1024x1024`). Intentionally avoid another dimension source of truth for fallback; primary paths are unaffected.
- **④ Unified short-edge tiers (2K=1440, 4K=2160) change several pixel backends' existing outputs** (all with exact ratios):
  - OpenAI image 2K, 9:16: approximately `2048×3584` (4:7) → `1440×2560`, approximately halving pixels and lowering cost;
  - DashScope wan 4K, 9:16: `3072×5376` (long edge beyond 4K) → `2160×3840` (3840 long edge genuinely corresponds to 4K), approximately halving pixels;
  - DashScope wan/qwen 2K, 9:16: `1536×2688` (4:7 deviation) → `1440×2560` (exact, slightly smaller);
  - DashScope wan 1K, 9:16: `768×1344` → `1008×1792` (larger).
- **⑤ Some backends have irreducible inherent constraints and are legitimate exceptions**:
  - **Sora video**: size is a fixed enumeration, **not arbitrary WxH**. Video therefore bypasses `aspect_size` and **snaps to the legal tier with nearest ratio**. Legal tiers vary by model (verified by official model pages; openai SDK `VideoSize` Literals lag): sora-2 (base) has only exact 720p tiers (`720x1280`/`1280x720`), capping clarity at 720-class (clarity yields to ratio, consistent with the principle); sora-2-pro adds exact 1080p tiers (`1080x1920`/`1920x1080`). Dall-e-style 4:7 tiers (`1024x1792`/`1792x1024`) violate ratio priority and were removed (`lib/backends/video_backends/openai.py`). NewAPI video is a generic width/height aggregation endpoint using `aspect_size` (aligned to multiples of 8, producing standard dimensions such as `1920×1080`).
  - **Ark Seedream 2K**: retain approximately 0.1% ratio deviation due to minimum total pixels (≥3.68M), preserving existing exact tables (migration would reduce precision).
  - **DashScope qwen classic series** (`qwen-image`/`-plus`/`-max`): only five fixed tiers, no arbitrary pixels; no registered presets and not officially recommended, so outside this mechanism.
- **String-based backends with independent ratios need no dimension-logic changes**: Gemini/Grok/Vidu/ark/DashScope video pass `aspect_ratio`/`ratio` as independent SDK fields orthogonal to `resolution` (context7 verified Veo's independent `aspect_ratio` and `resolution`; Grok has the same shape). They do not encode ratio into pixel size and already have no dimension bug; add only ratio-independence guard tests and comments.

Like `docs/adr/0001`, this is "resolve generation parameters at execution": dimensions likewise derive exactly from ratio+resolution in the execution layer, without advance freezing.
