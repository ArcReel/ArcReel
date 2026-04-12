# Design Document: Preset Model Expansion

> Date: 2026-03-30
> Status: Approved

## Goal

Expand model tier coverage in `PROVIDER_REGISTRY`: add 12 model entries for the existing 4 providers, and fix 1 capabilities declaration error. After the change, total model count increases from 18 to 30.

## Change Scope

**Single file**: `lib/config/registry.py`

**Three operations:**

1. **Add 12 new ModelInfo entries** (each provider +3)
2. **Bugfix**: remove the incorrectly declared `structured_output` capability from `doubao-seed-2-0-lite-260215` (Seed 2.0 series does not support it)
3. **Ordering reorganization**: each provider's models dict grouped by `text → image → video`, within each group ordered by `flagship → balanced (default) → lightweight`

**Unchanged constraints:**

- All new models have `default=False`
- Each media_type still has exactly 1 default
- No new providers, no backend changes, no frontend changes

## Per-Provider Change Details

### gemini-aistudio (4 → 7)

gemini-vertex is a full mirror (same model IDs); listed only once below.

| Order | Model ID | Display Name | media_type | capabilities | default | Change |
|-------|----------|--------------|-----------|-------------|---------|--------|
| 1 | `gemini-3.1-pro-preview` | Gemini 3.1 Pro | text | text_generation, structured_output, vision | false | **New** |
| 2 | `gemini-3-flash-preview` | Gemini 3 Flash | text | text_generation, structured_output, vision | **true** | Existing |
| 3 | `gemini-3.1-flash-lite-preview` | Gemini 3.1 Flash Lite | text | text_generation, structured_output | false | **New** |
| 4 | `gemini-3-pro-image-preview` | Gemini 3 Pro Image | image | text_to_image, image_to_image | false | **New** |
| 5 | `gemini-3.1-flash-image-preview` | Gemini 3.1 Flash Image | image | text_to_image, image_to_image | **true** | Existing |
| 6 | `veo-3.1-generate-preview` | Veo 3.1 | video | (same as existing) | false | Existing |
| 7 | `veo-3.1-fast-generate-preview` | Veo 3.1 Fast | video | (same as existing) | **true** | Existing |

> gemini-vertex Veo model IDs use the `-001` suffix version; otherwise identical to aistudio.

### grok (4 → 7)

| Order | Model ID | Display Name | media_type | capabilities | default | Change |
|-------|----------|--------------|-----------|-------------|---------|--------|
| 1 | `grok-4.20-0309-reasoning` | Grok 4.20 Reasoning | text | text_generation, structured_output, vision | false | **New** |
| 2 | `grok-4.20-0309-non-reasoning` | Grok 4.20 Non-Reasoning | text | text_generation, structured_output, vision | false | **New** |
| 3 | `grok-4-1-fast-reasoning` | Grok 4.1 Fast Reasoning | text | text_generation, structured_output, vision | **true** | Existing |
| 4 | `grok-4-1-fast-non-reasoning` | Grok 4.1 Fast (Non-Reasoning) | text | text_generation, structured_output, vision | false | **New** |
| 5 | `grok-imagine-image-pro` | Grok Imagine Image Pro | image | text_to_image, image_to_image | false | Existing |
| 6 | `grok-imagine-image` | Grok Imagine Image | image | text_to_image, image_to_image | **true** | Existing |
| 7 | `grok-imagine-video` | Grok Imagine Video | video | text_to_video, image_to_video | **true** | Existing |

### ark (6 → 9)

| Order | Model ID | Display Name | media_type | capabilities | default | Change |
|-------|----------|--------------|-----------|-------------|---------|--------|
| 1 | `doubao-seed-2-0-pro-260215` | Doubao Seed 2.0 Pro | text | text_generation, vision | false | **New** |
| 2 | `doubao-seed-2-0-lite-260215` | Doubao Seed 2.0 Lite | text | text_generation, ~~structured_output~~, vision | **true** | **Bugfix: remove structured_output** |
| 3 | `doubao-seed-2-0-mini-260215` | Doubao Seed 2.0 Mini | text | text_generation, vision | false | **New** |
| 4 | `doubao-seed-1-8-251228` | Doubao Seed 1.8 | text | text_generation, structured_output, vision | false | **New** (structured output supplement) |
| 5 | `doubao-seedream-5-0-lite-260128` | Seedream 5.0 Lite | image | text_to_image, image_to_image | **true** | Existing |
| 6 | `doubao-seedream-5-0-260128` | Seedream 5.0 | image | text_to_image, image_to_image | false | Existing |
| 7 | `doubao-seedream-4-5-251128` | Seedream 4.5 | image | text_to_image, image_to_image | false | Existing |
| 8 | `doubao-seedream-4-0-250828` | Seedream 4.0 | image | text_to_image, image_to_image | false | Existing |
| 9 | `doubao-seedance-1-5-pro-251215` | Seedance 1.5 Pro | video | (same as existing) | **true** | Existing |

> Seed 1.8 is placed at the end of the text group: it is a supplementary model from the 1.x generation, whose core value is filling the structured_output capability gap; it is not on the Seed 2.0 tier line.

## Test Impact

- `test_each_media_type_has_default`: unaffected (each media_type still has exactly 1 default)
- `test_all_providers_have_text/image/video_models`: unaffected (additions only)
- No new test cases needed

## Out of Scope

The following items are not included in this change and will be handled in subsequent PRs:

- **Runtime capability validation**: when a user selects a model that doesn't support structured_output but the pipeline requires it, should fail early or fallback. This change only fixes the registry declaration.
- **ArkTextBackend capabilities hard-coding**: the backend-level `TextCapability.STRUCTURED_OUTPUT` declaration needs to change to model-level determination.
- **Frontend capabilities conditional rendering**: filter or indicate unavailable features based on the selected model's capabilities.
