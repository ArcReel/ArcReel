# Preset Model Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 12 model entries for 4 providers, fix the Seed 2.0 Lite capabilities declaration error, and reorder all models dicts by media_type grouping + tier arrangement.

**Architecture:** Pure registry data change; only modify the `models` field in `PROVIDER_REGISTRY` in `lib/config/registry.py`. All new models are supported by existing backends; no code logic changes are needed.

**Tech Stack:** Python dataclass (ModelInfo, ProviderMeta)

**Spec:** `docs/superpowers/specs/2026-03-30-model-expansion-design.md`

---

### Task 1: gemini-aistudio Provider — Add 3 Models + Reorder

**Files:**
- Modify: `lib/config/registry.py:33-65` (gemini-aistudio's models dict)

- [ ] **Step 1: Replace gemini-aistudio's models dict**

Replace the entire `models` field of `gemini-aistudio` with the following content (text → image → video, within each group: flagship → default → lightweight):

```python
models={
    # --- text ---
    "gemini-3.1-pro-preview": ModelInfo(
        display_name="Gemini 3.1 Pro",
        media_type="text",
        capabilities=["text_generation", "structured_output", "vision"],
    ),
    "gemini-3-flash-preview": ModelInfo(
        display_name="Gemini 3 Flash",
        media_type="text",
        capabilities=["text_generation", "structured_output", "vision"],
        default=True,
    ),
    "gemini-3.1-flash-lite-preview": ModelInfo(
        display_name="Gemini 3.1 Flash Lite",
        media_type="text",
        capabilities=["text_generation", "structured_output"],
    ),
    # --- image ---
    "gemini-3-pro-image-preview": ModelInfo(
        display_name="Gemini 3 Pro Image",
        media_type="image",
        capabilities=["text_to_image", "image_to_image"],
    ),
    "gemini-3.1-flash-image-preview": ModelInfo(
        display_name="Gemini 3.1 Flash Image",
        media_type="image",
        capabilities=["text_to_image", "image_to_image"],
        default=True,
    ),
    # --- video ---
    "veo-3.1-generate-preview": ModelInfo(
        display_name="Veo 3.1",
        media_type="video",
        capabilities=["text_to_video", "image_to_video", "negative_prompt", "video_extend"],
    ),
    "veo-3.1-fast-generate-preview": ModelInfo(
        display_name="Veo 3.1 Fast",
        media_type="video",
        capabilities=["text_to_video", "image_to_video", "negative_prompt", "video_extend"],
        default=True,
    ),
},
```

- [ ] **Step 2: Run tests to verify**

Run: `uv run python -m pytest tests/test_config_registry_models.py -v`
Expected: all PASS (7 models, 1 default each for text/image/video)

---

### Task 2: gemini-vertex Provider — Add 3 Models + Reorder

**Files:**
- Modify: `lib/config/registry.py:66-98` (gemini-vertex's models dict)

- [ ] **Step 1: Replace gemini-vertex's models dict**

Fully mirrors gemini-aistudio; the only difference is that Veo model IDs use the `-001` suffix:

```python
models={
    # --- text ---
    "gemini-3.1-pro-preview": ModelInfo(
        display_name="Gemini 3.1 Pro",
        media_type="text",
        capabilities=["text_generation", "structured_output", "vision"],
    ),
    "gemini-3-flash-preview": ModelInfo(
        display_name="Gemini 3 Flash",
        media_type="text",
        capabilities=["text_generation", "structured_output", "vision"],
        default=True,
    ),
    "gemini-3.1-flash-lite-preview": ModelInfo(
        display_name="Gemini 3.1 Flash Lite",
        media_type="text",
        capabilities=["text_generation", "structured_output"],
    ),
    # --- image ---
    "gemini-3-pro-image-preview": ModelInfo(
        display_name="Gemini 3 Pro Image",
        media_type="image",
        capabilities=["text_to_image", "image_to_image"],
    ),
    "gemini-3.1-flash-image-preview": ModelInfo(
        display_name="Gemini 3.1 Flash Image",
        media_type="image",
        capabilities=["text_to_image", "image_to_image"],
        default=True,
    ),
    # --- video ---
    "veo-3.1-generate-001": ModelInfo(
        display_name="Veo 3.1",
        media_type="video",
        capabilities=["text_to_video", "image_to_video", "generate_audio", "negative_prompt", "video_extend"],
    ),
    "veo-3.1-fast-generate-001": ModelInfo(
        display_name="Veo 3.1 Fast",
        media_type="video",
        capabilities=["text_to_video", "image_to_video", "generate_audio", "negative_prompt", "video_extend"],
        default=True,
    ),
},
```

- [ ] **Step 2: Run tests to verify**

Run: `uv run python -m pytest tests/test_config_registry_models.py -v`
Expected: all PASS

---

### Task 3: ark Provider — Add 3 Models + Bugfix + Reorder

**Files:**
- Modify: `lib/config/registry.py:99-143` (ark's models dict)

- [ ] **Step 1: Replace ark's models dict**

Note two key changes:
1. Remove `structured_output` from `doubao-seed-2-0-lite-260215` capabilities (bugfix)
2. Add three new text models: Pro, Mini, and Seed 1.8

```python
models={
    # --- text ---
    "doubao-seed-2-0-pro-260215": ModelInfo(
        display_name="Doubao Seed 2.0 Pro",
        media_type="text",
        capabilities=["text_generation", "vision"],
    ),
    "doubao-seed-2-0-lite-260215": ModelInfo(
        display_name="Doubao Seed 2.0 Lite",
        media_type="text",
        capabilities=["text_generation", "vision"],
        default=True,
    ),
    "doubao-seed-2-0-mini-260215": ModelInfo(
        display_name="Doubao Seed 2.0 Mini",
        media_type="text",
        capabilities=["text_generation", "vision"],
    ),
    "doubao-seed-1-8-251228": ModelInfo(
        display_name="Doubao Seed 1.8",
        media_type="text",
        capabilities=["text_generation", "structured_output", "vision"],
    ),
    # --- image ---
    "doubao-seedream-5-0-lite-260128": ModelInfo(
        display_name="Seedream 5.0 Lite",
        media_type="image",
        capabilities=["text_to_image", "image_to_image"],
        default=True,
    ),
    "doubao-seedream-5-0-260128": ModelInfo(
        display_name="Seedream 5.0",
        media_type="image",
        capabilities=["text_to_image", "image_to_image"],
    ),
    "doubao-seedream-4-5-251128": ModelInfo(
        display_name="Seedream 4.5",
        media_type="image",
        capabilities=["text_to_image", "image_to_image"],
    ),
    "doubao-seedream-4-0-250828": ModelInfo(
        display_name="Seedream 4.0",
        media_type="image",
        capabilities=["text_to_image", "image_to_image"],
    ),
    # --- video ---
    "doubao-seedance-1-5-pro-251215": ModelInfo(
        display_name="Seedance 1.5 Pro",
        media_type="video",
        capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "flex_tier"],
        default=True,
    ),
},
```

- [ ] **Step 2: Run tests to verify**

Run: `uv run python -m pytest tests/test_config_registry_models.py -v`
Expected: all PASS

---

### Task 4: grok Provider — Add 3 Models + Reorder

**Files:**
- Modify: `lib/config/registry.py:144-177` (grok's models dict)

- [ ] **Step 1: Replace grok's models dict**

```python
models={
    # --- text ---
    "grok-4.20-0309-reasoning": ModelInfo(
        display_name="Grok 4.20 Reasoning",
        media_type="text",
        capabilities=["text_generation", "structured_output", "vision"],
    ),
    "grok-4.20-0309-non-reasoning": ModelInfo(
        display_name="Grok 4.20 Non-Reasoning",
        media_type="text",
        capabilities=["text_generation", "structured_output", "vision"],
    ),
    "grok-4-1-fast-reasoning": ModelInfo(
        display_name="Grok 4.1 Fast Reasoning",
        media_type="text",
        capabilities=["text_generation", "structured_output", "vision"],
        default=True,
    ),
    "grok-4-1-fast-non-reasoning": ModelInfo(
        display_name="Grok 4.1 Fast (Non-Reasoning)",
        media_type="text",
        capabilities=["text_generation", "structured_output", "vision"],
    ),
    # --- image ---
    "grok-imagine-image-pro": ModelInfo(
        display_name="Grok Imagine Image Pro",
        media_type="image",
        capabilities=["text_to_image", "image_to_image"],
    ),
    "grok-imagine-image": ModelInfo(
        display_name="Grok Imagine Image",
        media_type="image",
        capabilities=["text_to_image", "image_to_image"],
        default=True,
    ),
    # --- video ---
    "grok-imagine-video": ModelInfo(
        display_name="Grok Imagine Video",
        media_type="video",
        capabilities=["text_to_video", "image_to_video"],
        default=True,
    ),
},
```

- [ ] **Step 2: Run tests to verify**

Run: `uv run python -m pytest tests/test_config_registry_models.py -v`
Expected: all PASS

---

### Task 5: Full Test + Commit

**Files:**
- No new files

- [ ] **Step 1: Run full registry tests**

Run: `uv run python -m pytest tests/test_config_registry.py tests/test_config_registry_models.py -v`
Expected: all PASS

- [ ] **Step 2: Verify total model count**

Run: `uv run python -c "from lib.config.registry import PROVIDER_REGISTRY; total = sum(len(m.models) for m in PROVIDER_REGISTRY.values()); print(f'Total models: {total}'); assert total == 30, f'Expected 30, got {total}'"`
Expected: `Total models: 30`

- [ ] **Step 3: Commit**

```bash
git add lib/config/registry.py
git commit -m "feat: expand preset model coverage (+12 models) and fix Seed 2.0 Lite capabilities

- gemini-aistudio/vertex: +Gemini 3.1 Pro, +Gemini 3.1 Flash Lite, +Gemini 3 Pro Image
- grok: +Grok 4.20 Reasoning/Non-Reasoning, +Grok 4.1 Fast Non-Reasoning
- ark: +Seed 2.0 Pro/Mini, +Seed 1.8 (structured output supplement)
- bugfix: Seed 2.0 Lite - remove incorrectly declared structured_output capability
- All provider models dicts reordered by text→image→video + tier arrangement"
```
