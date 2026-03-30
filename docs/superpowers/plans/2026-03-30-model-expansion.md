# 预置模型扩充 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 4 个供应商新增 12 个模型条目，修正 Seed 2.0 Lite 的 capabilities 声明错误，并重排所有 models dict 为 media_type 分组 + 梯度排列。

**Architecture:** 纯 registry 数据变更，仅修改 `lib/config/registry.py` 中 `PROVIDER_REGISTRY` 的 `models` 字段。所有新模型已被现有 Backend 支持，无需任何代码逻辑改动。

**Tech Stack:** Python dataclass（ModelInfo, ProviderMeta）

**Spec:** `docs/superpowers/specs/2026-03-30-model-expansion-design.md`

---

### Task 1: gemini-aistudio 供应商 — 新增 3 个模型 + 重排

**Files:**
- Modify: `lib/config/registry.py:33-65`（gemini-aistudio 的 models dict）

- [ ] **Step 1: 替换 gemini-aistudio 的 models dict**

将 `gemini-aistudio` 的 `models` 字段整体替换为以下内容（text → image → video，组内旗舰 → default → 轻量）：

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

- [ ] **Step 2: 运行测试验证**

Run: `uv run python -m pytest tests/test_config_registry_models.py -v`
Expected: 全部 PASS（7 个模型，text/image/video 各有 1 个 default）

---

### Task 2: gemini-vertex 供应商 — 新增 3 个模型 + 重排

**Files:**
- Modify: `lib/config/registry.py:66-98`（gemini-vertex 的 models dict）

- [ ] **Step 1: 替换 gemini-vertex 的 models dict**

与 gemini-aistudio 完全镜像，唯一区别是 Veo 模型 ID 使用 `-001` 后缀：

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

- [ ] **Step 2: 运行测试验证**

Run: `uv run python -m pytest tests/test_config_registry_models.py -v`
Expected: 全部 PASS

---

### Task 3: ark 供应商 — 新增 3 个模型 + bugfix + 重排

**Files:**
- Modify: `lib/config/registry.py:99-143`（ark 的 models dict）

- [ ] **Step 1: 替换 ark 的 models dict**

注意两个关键变更：
1. `doubao-seed-2-0-lite-260215` 的 capabilities 移除 `structured_output`（bugfix）
2. 新增 Pro、Mini、Seed 1.8 三个文本模型

```python
models={
    # --- text ---
    "doubao-seed-2-0-pro-260215": ModelInfo(
        display_name="豆包 Seed 2.0 Pro",
        media_type="text",
        capabilities=["text_generation", "vision"],
    ),
    "doubao-seed-2-0-lite-260215": ModelInfo(
        display_name="豆包 Seed 2.0 Lite",
        media_type="text",
        capabilities=["text_generation", "vision"],
        default=True,
    ),
    "doubao-seed-2-0-mini-260215": ModelInfo(
        display_name="豆包 Seed 2.0 Mini",
        media_type="text",
        capabilities=["text_generation", "vision"],
    ),
    "doubao-seed-1-8-251228": ModelInfo(
        display_name="豆包 Seed 1.8",
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

- [ ] **Step 2: 运行测试验证**

Run: `uv run python -m pytest tests/test_config_registry_models.py -v`
Expected: 全部 PASS

---

### Task 4: grok 供应商 — 新增 3 个模型 + 重排

**Files:**
- Modify: `lib/config/registry.py:144-177`（grok 的 models dict）

- [ ] **Step 1: 替换 grok 的 models dict**

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

- [ ] **Step 2: 运行测试验证**

Run: `uv run python -m pytest tests/test_config_registry_models.py -v`
Expected: 全部 PASS

---

### Task 5: 全量测试 + 提交

**Files:**
- 无新增文件

- [ ] **Step 1: 运行全量 registry 测试**

Run: `uv run python -m pytest tests/test_config_registry.py tests/test_config_registry_models.py -v`
Expected: 全部 PASS

- [ ] **Step 2: 验证模型总数**

Run: `uv run python -c "from lib.config.registry import PROVIDER_REGISTRY; total = sum(len(m.models) for m in PROVIDER_REGISTRY.values()); print(f'Total models: {total}'); assert total == 30, f'Expected 30, got {total}'"`
Expected: `Total models: 30`

- [ ] **Step 3: 提交**

```bash
git add lib/config/registry.py
git commit -m "feat: 扩充预置模型覆盖（+12 模型）并修正 Seed 2.0 Lite capabilities

- gemini-aistudio/vertex: +Gemini 3.1 Pro, +Gemini 3.1 Flash Lite, +Gemini 3 Pro Image
- grok: +Grok 4.20 Reasoning/Non-Reasoning, +Grok 4.1 Fast Non-Reasoning
- ark: +Seed 2.0 Pro/Mini, +Seed 1.8（结构化输出补充）
- bugfix: Seed 2.0 Lite 移除错误声明的 structured_output capability
- 所有供应商 models dict 按 text→image→video + 梯度排列重整"
```
