# 设计文档：预置模型扩充

> 日期：2026-03-30
> 状态：已批准

## 目标

扩充 `PROVIDER_REGISTRY` 的模型梯度覆盖：为现有 4 个供应商新增 12 个模型条目，并修正 1 个 capabilities 声明错误。变更后模型总数从 18 增至 30。

## 变更范围

**单文件**：`lib/config/registry.py`

**三类操作**：

1. **新增 12 个 ModelInfo 条目**（每个供应商 +3）
2. **Bugfix**：`doubao-seed-2-0-lite-260215` 移除错误声明的 `structured_output` capability（Seed 2.0 全系不支持）
3. **排列重整**：每个供应商的 models dict 按 `text → image → video` 分组，组内按 `旗舰 → 均衡(default) → 轻量` 排列

**不变的约束**：

- 所有新增模型 `default=False`
- 每个 media_type 仍只有 1 个 default
- 不新增供应商、不改 Backend、不改前端

## 各供应商变更明细

### gemini-aistudio（4 → 7）

gemini-vertex 与其完全镜像（模型 ID 相同），下方仅列一次。

| 顺序 | 模型 ID | 显示名 | media_type | capabilities | default | 变更 |
|------|---------|--------|-----------|-------------|---------|------|
| 1 | `gemini-3.1-pro-preview` | Gemini 3.1 Pro | text | text_generation, structured_output, vision | false | **新增** |
| 2 | `gemini-3-flash-preview` | Gemini 3 Flash | text | text_generation, structured_output, vision | **true** | 现有 |
| 3 | `gemini-3.1-flash-lite-preview` | Gemini 3.1 Flash Lite | text | text_generation, structured_output | false | **新增** |
| 4 | `gemini-3-pro-image-preview` | Gemini 3 Pro Image | image | text_to_image, image_to_image | false | **新增** |
| 5 | `gemini-3.1-flash-image-preview` | Gemini 3.1 Flash Image | image | text_to_image, image_to_image | **true** | 现有 |
| 6 | `veo-3.1-generate-preview` | Veo 3.1 | video | 同现有 | false | 现有 |
| 7 | `veo-3.1-fast-generate-preview` | Veo 3.1 Fast | video | 同现有 | **true** | 现有 |

> gemini-vertex 的 Veo 模型 ID 为 `-001` 后缀版本，其余与 aistudio 相同。

### grok（4 → 7）

| 顺序 | 模型 ID | 显示名 | media_type | capabilities | default | 变更 |
|------|---------|--------|-----------|-------------|---------|------|
| 1 | `grok-4.20-0309-reasoning` | Grok 4.20 Reasoning | text | text_generation, structured_output, vision | false | **新增** |
| 2 | `grok-4.20-0309-non-reasoning` | Grok 4.20 Non-Reasoning | text | text_generation, structured_output, vision | false | **新增** |
| 3 | `grok-4-1-fast-reasoning` | Grok 4.1 Fast Reasoning | text | text_generation, structured_output, vision | **true** | 现有 |
| 4 | `grok-4-1-fast-non-reasoning` | Grok 4.1 Fast (Non-Reasoning) | text | text_generation, structured_output, vision | false | **新增** |
| 5 | `grok-imagine-image-pro` | Grok Imagine Image Pro | image | text_to_image, image_to_image | false | 现有 |
| 6 | `grok-imagine-image` | Grok Imagine Image | image | text_to_image, image_to_image | **true** | 现有 |
| 7 | `grok-imagine-video` | Grok Imagine Video | video | text_to_video, image_to_video | **true** | 现有 |

### ark（6 → 9）

| 顺序 | 模型 ID | 显示名 | media_type | capabilities | default | 变更 |
|------|---------|--------|-----------|-------------|---------|------|
| 1 | `doubao-seed-2-0-pro-260215` | 豆包 Seed 2.0 Pro | text | text_generation, vision | false | **新增** |
| 2 | `doubao-seed-2-0-lite-260215` | 豆包 Seed 2.0 Lite | text | text_generation, ~~structured_output~~, vision | **true** | **bugfix：移除 structured_output** |
| 3 | `doubao-seed-2-0-mini-260215` | 豆包 Seed 2.0 Mini | text | text_generation, vision | false | **新增** |
| 4 | `doubao-seed-1-8-251228` | 豆包 Seed 1.8 | text | text_generation, structured_output, vision | false | **新增**（结构化输出补充） |
| 5 | `doubao-seedream-5-0-lite-260128` | Seedream 5.0 Lite | image | text_to_image, image_to_image | **true** | 现有 |
| 6 | `doubao-seedream-5-0-260128` | Seedream 5.0 | image | text_to_image, image_to_image | false | 现有 |
| 7 | `doubao-seedream-4-5-251128` | Seedream 4.5 | image | text_to_image, image_to_image | false | 现有 |
| 8 | `doubao-seedream-4-0-250828` | Seedream 4.0 | image | text_to_image, image_to_image | false | 现有 |
| 9 | `doubao-seedance-1-5-pro-251215` | Seedance 1.5 Pro | video | 同现有 | **true** | 现有 |

> Seed 1.8 排在文本组末尾：它是 1.x 代补充模型，核心价值是填补 structured_output 能力空缺，不在 Seed 2.0 梯度线上。

## 测试影响

- `test_each_media_type_has_default`：不受影响（每个 media_type 仍只有 1 个 default）
- `test_all_providers_have_text/image/video_models`：不受影响（只增不减）
- 无需新增测试用例

## 超出范围

以下事项不在本次变更中，将在后续 PR 处理：

- **运行时 capability 校验**：当用户选择不支持 structured_output 的模型但 pipeline 需要时，应提前报错或 fallback。本次仅修正 registry 声明。
- **ArkTextBackend capabilities 硬编码**：后端级别声明的 `TextCapability.STRUCTURED_OUTPUT` 需改为模型级别判断。
- **前端 capabilities 条件渲染**：根据所选模型的 capabilities 过滤或提示不可用功能。
