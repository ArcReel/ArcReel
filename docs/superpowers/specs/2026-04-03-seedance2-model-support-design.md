# Seedance 2.0 Model Support Design

> Related Issue: [ArcReel/ArcReel#42](https://github.com/ArcReel/ArcReel/issues/42)
> Date: 2026-04-03
> Scope: Minimum viable (model registration + pricing + capability declaration)

## Background

Seedance 2.0 is now available for enterprise beta testing. The current Ark video backend only registers Seedance 1.5 Pro (`doubao-seedance-1-5-pro-251215`). Two models need to be added — Seedance 2.0 and 2.0 Fast — so users can select them in configuration.

This change does not cover Seedance 2.0's new capability extensions (multimodal reference images, video editing/extension, web search, etc.); it only enables existing t2v and i2v (first frame) pipelines to run correctly on 2.0 models.

## Change List

### 1. Model Registration — `lib/config/registry.py`

In the ark provider's `models` dict, add immediately after `doubao-seedance-1-5-pro-251215`:

```python
"doubao-seedance-2-0-260128": ModelInfo(
    display_name="Seedance 2.0",
    media_type="video",
    capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "video_extend"],
),
"doubao-seedance-2-0-fast-260128": ModelInfo(
    display_name="Seedance 2.0 Fast",
    media_type="video",
    capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "video_extend"],
),
```

- `default=True` is retained on 1.5 Pro; the default model is not changed
- 2.0 series declares `video_extend`, does not declare `flex_tier`

### 2. Capability Mapping — `lib/video_backends/ark.py`

Add a model → capability mapping table to replace the hardcoded capabilities in `__init__`:

```python
_MODEL_CAPABILITIES: dict[str, set[VideoCapability]] = {
    "doubao-seedance-2-0-260128": {
        VideoCapability.TEXT_TO_VIDEO,
        VideoCapability.IMAGE_TO_VIDEO,
        VideoCapability.GENERATE_AUDIO,
        VideoCapability.SEED_CONTROL,
        VideoCapability.VIDEO_EXTEND,
    },
    "doubao-seedance-2-0-fast-260128": {
        VideoCapability.TEXT_TO_VIDEO,
        VideoCapability.IMAGE_TO_VIDEO,
        VideoCapability.GENERATE_AUDIO,
        VideoCapability.SEED_CONTROL,
        VideoCapability.VIDEO_EXTEND,
    },
}

_DEFAULT_CAPABILITIES = {
    VideoCapability.TEXT_TO_VIDEO,
    VideoCapability.IMAGE_TO_VIDEO,
    VideoCapability.GENERATE_AUDIO,
    VideoCapability.SEED_CONTROL,
    VideoCapability.FLEX_TIER,
}
```

In `__init__`:
```python
self._capabilities = self._MODEL_CAPABILITIES.get(self._model, self._DEFAULT_CAPABILITIES)
```

The `generate` method requires no changes; the Ark SDK call parameters for 2.0 are compatible with 1.5.

### 3. Pricing — `lib/cost_calculator.py`

Add to `ARK_VIDEO_COST`:

```python
"doubao-seedance-2-0-260128": {
    ("default", True): 46.00,
    ("default", False): 46.00,
},
"doubao-seedance-2-0-fast-260128": {
    ("default", True): 37.00,
    ("default", False): 37.00,
},
```

- 2.0 is actually priced based on "whether input contains video"; current scope has no video input, so 46.00/37.00 is used uniformly
- `generate_audio` dimension is set to the same value (2.0 audio does not affect pricing)
- No flex entries (2.0 does not support offline inference)
- `calculate_ark_video_cost` method requires no changes

### 4. Testing

Extend existing test files; no new files added:

- **`test_config_registry.py`**: update expected ark video model count (if assertions exist)
- **`test_video_backend_ark.py`**: parameterized test verifying 2.0 models get correct capabilities (has `video_extend`, no `flex_tier`)
- **`test_cost_calculator.py`** (if it exists): add 2.0 model cost calculation assertions

## Out of Scope

- Prompt adapters (remaining items from Issue #42, handled separately)
- Seedance 2.0 new capabilities: first/last frame, multimodal reference images, video editing/extension, web search
- `VideoGenerationRequest` extension (reference image/video fields)
- Default model change
- Resolution validation (2.0 does not support 1080p, but Ark default is already 720p; no additional validation for now)
