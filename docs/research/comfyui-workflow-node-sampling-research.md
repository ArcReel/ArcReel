# 主流生图 / 生视频 workflow 的输入输出节点形态抽样

**调研日期**：2026-09-17
**议题**：[#2520](https://github.com/ArcReel/ArcReel/issues/2520)（地图 [#2516](https://github.com/ArcReel/ArcReel/issues/2516)）
**用途**：为「用户导入自有 API 格式 workflow，自动推断槽位绑定」提供事实基础
**一手来源**：
- ComfyUI 官方模板库 [`Comfy-Org/workflow_templates`](https://github.com/Comfy-Org/workflow_templates)（`templates/*.json`，抓取于 2026-09-17）
- ComfyUI 主仓源码 [`comfyanonymous/ComfyUI`](https://github.com/comfyanonymous/ComfyUI) `master` 分支：`nodes.py`、`comfy_extras/nodes_wan.py`、`nodes_hunyuan.py`、`nodes_lt.py`、`nodes_video.py`、`nodes_images.py`、`nodes_qwen.py`、`nodes_flux.py`、`nodes_sd3.py`、`nodes_edit_model.py`、`nodes_custom_sampler.py`、`nodes_video_model.py`
- [docs.comfy.org](https://docs.comfy.org) 的 `development/api-development/workflow-api-format.mdx` 与 `built-in-nodes/` 节点参考
- 社区节点包源码：[`Kosinkadink/ComfyUI-VideoHelperSuite`](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite)、[`kijai/ComfyUI-WanVideoWrapper`](https://github.com/kijai/ComfyUI-WanVideoWrapper)（含其 `example_workflows/`）
- 前端 subgraph 行为：[`comfy-org/comfyui_frontend`](https://github.com/comfy-org/comfyui_frontend) 的 `docs/architecture/` 与 `docs/adr/`

**样本量**：19 个 ComfyUI 官方模板 + 3 个 Kijai WanVideoWrapper 社区 workflow（清单见 §9）。

**不在范围**：ArcReel 侧的数据模型、UI、实施计划。本文只陈述抽样事实与由事实直接推出的推断规则约束。

---

## 0. 结论先行

1. **槽位的 input 名比 class_type 收敛得多。** 尺寸在 22 个样本里 100% 叫 `width` / `height`；帧数只有 3 个名字（`length` / `num_frames` / `video_frames`）；提示词只有 4 个（`text` / `prompt` / `positive_prompt` / `negative_prompt`）。但承载这些字段的 `class_type` 超过 15 种。**推断规则应以 input 名为主键，class_type 只作为消歧与优先级信号。**
2. **必须要求用户导入 API 格式，不要尝试解析 UI 格式。** UI 格式的 `widgets_values` 是位置数组，且 `control_after_generate` 会插入一个额外元素，位置与 `INPUT_TYPES` 顺序不是一一对应（见 §1.3）。
3. **官方新模板大量使用 subgraph。** 19 个官方样本里 7 个把整条主链封进 `definitions.subgraphs`，UI 格式的顶层只剩 `LoadImage` 和 `SaveVideo`。API 格式会把 subgraph 展平，这恰好是 ArcReel 只接受 API 格式的第二个理由。
4. **时长不能用 `length / fps`，必须用 `(length - 1) / fps`。** 13 个视频样本全部满足 `length ≡ 1 (mod 4)`，且 `(length-1)/fps` 在 12/13 个样本里得到整数秒；`length/fps` 一个都得不到整数（见 §7）。
5. **一个 workflow 里出现多个同类节点是常态而非例外**（19 个官方样本中 8 个）。首选区分办法是**从采样器的 `positive` / `negative` 端口反向追溯**，而不是看标题；但社区 workflow 里采样器可能没有这两个端口，需要并列的字段名判据（见 §5）。
6. **有些 workflow 根本没有尺寸槽位。** Flux Kontext 与 Qwen-Image-Edit 的输出尺寸由输入图经 `FluxKontextImageScale` 推导，图里没有任何 `width` / `height`。推断规则必须允许「尺寸槽位缺失」这一合法结果。
7. **社区 workflow 几乎在每一条上都和官方模板相反**（§3）：帧数叫 `num_frames`、正负提示词挤在同一个节点的两个字段里、采样器没有 `positive` / `negative` 端口、fps 与产物同处 `VHS_VideoCombine` 一个节点。只按官方模板设计的推断规则会在社区 workflow 上大面积失效，**必须拿这组样本做验收用例**。

---

## 1. 方法：UI 格式与 API 格式的对应关系

### 1.1 两种格式

官方文档明确区分两种序列化格式（`development/api-development/workflow-api-format.mdx`）：

> ComfyUI workflows are JSON objects describing a graph of nodes. When calling ComfyUI programmatically, the workflow must be submitted in API format, a specialized JSON structure that differs from the regular save format used in the browser.

| | save 格式（UI） | API 格式 |
|---|---|---|
| 顶层结构 | `{nodes: [...], links: [...], groups, extra, definitions}` | `{"<node_id>": {inputs, class_type, _meta}}` |
| 节点标识 | `nodes[].id` + `nodes[].type` | 对象 key 是 node id，`class_type` 是节点类型 |
| 字段值 | `widgets_values` 位置数组 | `inputs` 具名字典 |
| 连线 | 独立的 `links` 数组 | `inputs.<name> = ["<上游 node id>", <输出槽位序号>]` |
| 标题 | `nodes[].title`（缺省时为空串） | `_meta.title`（缺省时填节点的 display name） |
| 布局信息 | 有（pos / size / color / groups） | 无 |

导出路径：前端 `File → Export Workflow (API)`。

**`class_type` 的取值就是节点注册名**：V1 节点取 `NODE_CLASS_MAPPINGS` 的 key（`nodes.py:2069` 起），V3 节点取 `define_schema()` 里的 `node_id=`（如 `nodes_video.py:139` 的 `node_id="SaveVideo"`）。抽样确认核心节点的 mapping key 与类名一致（`"CLIPTextEncode": CLIPTextEncode` 等）。

### 1.2 官方模板库存的是 UI 格式

`templates/*.json` 全部是 save 格式（含 `links` / `widgets_values`）。本文所有样本表里的「input 名」均取自源码 `INPUT_TYPES`（V1）或 `define_schema().inputs`（V3），即 API 格式里会出现的键名；「值」取自模板的 `widgets_values` 或连线。

### 1.3 为什么不能按位置解析 `widgets_values`

`KSampler.INPUT_TYPES` 的 widget 型输入依次是 `seed, steps, cfg, sampler_name, scheduler, denoise`，共 6 个（`nodes.py`）。但模板里实际是 7 个元素：

```json
"widgets_values": [82628696717253, "randomize", 30, 6, "uni_pc", "simple", 1]
```

多出来的 `"randomize"` 来自 `seed` 上的 `control_after_generate: True`。同样的偏移出现在 `KSamplerAdvanced.noise_seed`、`RandomNoise.noise_seed`、`SamplerCustom.noise_seed`（`nodes_custom_sampler.py:742, 1009`）以及 `PrimitiveInt`。**任何按下标取 `widgets_values` 的实现都会在带种子的节点上错位。** API 格式没有这个问题。

### 1.4 subgraph

19 个官方样本中 7 个使用 subgraph：`flux_dev_full_text_to_image`、`flux_kontext_dev_basic`、`image_qwen_image`、`image_qwen_image_edit_2509`、`video_wan2_2_14B_t2v`、`video_wan2_2_14B_i2v`、`video_wan_vace_flf2v`。

UI 格式里它们表现为一个 `type` 是 UUID 的节点，真实节点藏在 `definitions.subgraphs[].nodes`，并通过 `definitions.subgraphs[].inputs` 的具名端口（如 `text` / `width` / `height` / `seed` / `noise_seed` / `start_image`）把内部 widget 提升到外层。前端架构文档说明执行时边界会被溶解：

> Demonstrates how the execution logic flattens the subgraph hierarchy, treating it as transparent and dissolving the nesting boundary for a flat execution order.
> —— `comfyui_frontend/docs/architecture/subgraph-boundaries-and-promotion.md`

因此 API 格式里看到的是展平后的普通节点，ArcReel 无需实现 subgraph 展开。本文的统计一律按展平后计算。

---

## 2. 逐样本节点形态

每张表的列含义：槽位 → `class_type` / API `inputs` 里的字段名 / 该节点的默认标题（`_meta.title` 缺省值）/ 备注。**粗体**标注非默认标题。

### 2.1 Wan 2.1 文生视频 —— `text_to_video_wan.json`

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正提示词 | `CLIPTextEncode` | `text` | CLIP Text Encode (Prompt) | 标题被改成 **CLIP Text Encode (Positive Prompt)** |
| 负提示词 | `CLIPTextEncode` | `text` | 同上 | 标题 **CLIP Text Encode (Negative Prompt)**，中文默认负向词 |
| 宽 / 高 | `EmptyHunyuanLatentVideo` | `width` / `height` | Empty HunyuanVideo 1.0 Latent | 832 × 480。**Wan 复用了 Hunyuan 的 latent 节点** |
| 帧数 | `EmptyHunyuanLatentVideo` | `length` | 同上 | 33 |
| 种子 | `KSampler` | `seed` | KSampler | 82628696717253 |
| fps | `CreateVideo` | `fps` | Create Video | 16 |
| 产物 | `SaveVideo` | `filename_prefix` | Save Video | `video/ComfyUI`，`format=auto`，`codec=auto` |

首帧 / 尾帧 / 参考图：无。

### 2.2 Wan 2.1 图生视频 —— `image_to_video_wan.json`

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正 / 负提示词 | `CLIPTextEncode` ×2 | `text` | CLIP Text Encode (Prompt) | 标题带 Positive / Negative |
| 首帧 | `LoadImage` → `WanImageToVideo` | `image` → `start_image` | Load Image | 同一张图另接 `CLIPVisionEncode.image` |
| 宽 / 高 / 帧数 | `WanImageToVideo` | `width` / `height` / `length` | —— | 512 × 512，33 帧 |
| 种子 | `KSampler` | `seed` | KSampler | —— |
| fps | `CreateVideo` | `fps` | Create Video | 16 |
| 产物 | `SaveVideo` | `filename_prefix` | Save Video | `video/ComfyUI` |

`WanImageToVideo` 的完整 input 列表（`nodes_wan.py`）：`positive, negative, vae, width(832), height(480), length(81, step=4), batch_size(1), clip_vision_output?, start_image?`。

### 2.3 Wan 2.1 首尾帧 —— `wan2.1_flf2v_720_f16.json`

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正 / 负提示词 | `CLIPTextEncode` ×2 | `text` | CLIP Text Encode (Prompt) | —— |
| 首帧 | `LoadImage` → `WanFirstLastFrameToVideo` | `image` → `start_image` | Load Image | 节点标题被改成 **Start_image** |
| 尾帧 | `LoadImage` → `WanFirstLastFrameToVideo` | `image` → `end_image` | Load Image | 节点标题被改成 **End_image** |
| 宽 / 高 / 帧数 | `WanFirstLastFrameToVideo` | `width` / `height` / `length` | —— | 720 × 1280，33 帧 |
| 种子 | `KSampler` | `seed` | KSampler | —— |
| fps | `CreateVideo` | `fps` | Create Video | 16 |
| 产物 | `SaveVideo` | `filename_prefix` | Save Video | —— |

`WanFirstLastFrameToVideo` 的 input：`positive, negative, vae, width, height, length, batch_size, clip_vision_start_image?, clip_vision_end_image?, start_image?, end_image?`。两张图还分别经 `CLIPVisionEncode` 接到 `clip_vision_start_image` / `clip_vision_end_image`。

### 2.4 Wan 2.2 14B 文生视频 —— `video_wan2_2_14B_t2v.json`（subgraph）

subgraph 名 `Text to Video(Wan2.2)`，提升到外层的端口：`text, width, height, value, unet_name, lora_name, unet_name_1, lora_name_1, clip_name, vae_name, value_1`。

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正提示词 | `CLIPTextEncode` | `text` | CLIP Text Encode (Prompt) | 标题 **CLIP Text Encode (Positive Prompt)**，`text` 被提升 |
| 负提示词 | `CLIPTextEncode` | `text` | 同上 | 标题 **…(Negative Prompt)**，字面量、未提升 |
| 宽 / 高 | `EmptyHunyuanLatentVideo` | `width` / `height` | Empty HunyuanVideo 1.0 Latent | 640 × 640，被提升 |
| 帧数 | `EmptyHunyuanLatentVideo` | `length` | 同上 | **不是字面量**，由 `ComfyMathExpression` 算出 |
| 时长 → 帧数 | `ComfyMathExpression` | `expression` | —— | `floor(a * b) + 1`，a = Duration(5)，b = FPS(16) → 81 |
| 种子 | `KSamplerAdvanced` ×2 | `noise_seed` | KSampler (Advanced) | 高噪 923510416338945；低噪 0 且 `add_noise=disable` |
| fps | `CreateVideo` | `fps` | Create Video | 连到 `PrimitiveFloat`（16），非字面量 |
| 产物 | `SaveVideo` | `filename_prefix` | Save Video | `video/ComfyUI` |

### 2.5 Wan 2.2 14B 图生视频 —— `video_wan2_2_14B_i2v.json`（subgraph）

subgraph 名 `Image to Video (Wan2.2)`，端口含 `start_image, text, width, height, value_1, noise_seed, …`。

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正 / 负提示词 | `CLIPTextEncode` ×2 | `text` | CLIP Text Encode (Prompt) | 正向的 `text` 被提升 |
| 首帧 | `LoadImage`（顶层）→ `WanImageToVideo` | `image` → `start_image` | Load Image | 跨 subgraph 边界 |
| 宽 / 高 | `WanImageToVideo` | `width` / `height` | —— | 640 × 640，被提升 |
| 帧数 | `WanImageToVideo` | `length` | —— | `ComfyMathExpression` = `floor (a * b + 1)` → 81 |
| 种子 | `KSamplerAdvanced` ×2 | `noise_seed` | KSampler (Advanced) | 高噪档被提升为端口 `noise_seed` |
| fps | `CreateVideo` | `fps` | Create Video | 连到 `PrimitiveFloat` 标题 **Float (FPS)** = 16 |
| 产物 | `SaveVideo` | `filename_prefix` | Save Video | `video/Wan2.2_i2v` |

注意两个官方模板的换算式写法不同：t2v 是 `floor(a * b) + 1`，i2v 是 `floor (a * b + 1)`。当前参数下两者都得 81，但语义不等价。

### 2.6 Wan 2.2 14B 首尾帧 —— `video_wan2_2_14B_flf2v.json`

**这个文件里有两条完整的并行分支**（4 个 `CLIPTextEncode`、4 个 `LoadImage`、2 个 `WanFirstLastFrameToVideo`、4 个 `KSamplerAdvanced`、2 组 `CreateVideo` + `SaveVideo`），分别对应「4 步 LoRA 加速」和「20 步完整」两套参数。

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正提示词 | `CLIPTextEncode` ×2 | `text` | CLIP Text Encode (Prompt) | 两条分支各一个，标题均带 Positive |
| 负提示词 | `CLIPTextEncode` ×2 | `text` | 同上 | 同 |
| 首帧 / 尾帧 | `LoadImage` ×4 → `WanFirstLastFrameToVideo` ×2 | `start_image` / `end_image` | Load Image | 标题全为空，只能靠连线端口区分 |
| 宽 / 高 / 帧数 | `WanFirstLastFrameToVideo` ×2 | `width` / `height` / `length` | —— | 均 640 × 640，81 帧 |
| 种子 | `KSamplerAdvanced` ×4 | `noise_seed` | KSampler (Advanced) | 每分支「高噪 + 低噪」两段 |
| fps | `CreateVideo` ×2 | `fps` | Create Video | 均 16 |
| 产物 | `SaveVideo` ×2 | `filename_prefix` | Save Video | 均 `video/ComfyUI` |

`clip_vision_start_image` / `clip_vision_end_image` 在本模板中未连接（Wan 2.2 不再需要 CLIP Vision）。

### 2.7 Wan 2.2 5B 图文生视频 —— `video_wan2_2_5B_ti2v.json`

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正 / 负提示词 | `CLIPTextEncode` ×2 | `text` | CLIP Text Encode (Prompt) | **直接接 `KSampler.positive` / `.negative`**，不经中间节点 |
| 首帧 | `LoadImage` → `Wan22ImageToVideoLatent` | `image` → `start_image` | Load Image | 可选；留空即为纯文生 |
| 宽 / 高 / 帧数 | `Wan22ImageToVideoLatent` | `width` / `height` / `length` | —— | 1280 × 704，121 帧 |
| 种子 | `KSampler` | `seed` | KSampler | —— |
| fps | `CreateVideo` | `fps` | Create Video | 24 |
| 产物 | `SaveVideo` | `filename_prefix` | Save Video | —— |

`Wan22ImageToVideoLatent` 的 input 只有 `vae, width(1280), height(704), length(49, step=4), batch_size, start_image?` —— **它不吃 conditioning**，所以本模板里提示词节点直连采样器。这是 Wan 家族里唯一一个「图像条件节点不在提示词链路上」的形态。

### 2.8 Wan 2.1 VACE 首尾帧 —— `video_wan_vace_flf2v.json`（subgraph）

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正 / 负提示词 | `CLIPTextEncode` ×2 | `text` | CLIP Text Encode (Prompt) | 两者的 `text` 都被提升为 subgraph 端口 |
| 首帧 / 尾帧 | `LoadImage` ×2 | `image` | Load Image | 标题 **First Frame** / **Last Frame**；**不走 `start_image`/`end_image`** |
| 首尾帧注入 | `WanVaceToVideo` | `control_video` + `control_masks` | —— | 两帧被拼成图像批次后作为控制视频送入 |
| 参考图 | `WanVaceToVideo` | `reference_image` | —— | 本模板未连接 |
| 宽 / 高 | `WanVaceToVideo` | `width` / `height` | —— | 由 `max(4, (int(a) // 16) * 16)` 从 512 对齐 |
| 帧数 | `WanVaceToVideo` | `length` | —— | `a * 16 + 1`，a = Duration(5) → 81 |
| 种子 | `KSampler` | `seed` | KSampler | —— |
| fps | `CreateVideo` | `fps` | Create Video | 16 |
| 产物 | `SaveVideo` | `filename_prefix` | Save Video | `video/wan2.1_vace`，`codec=h264` |

**VACE 是首尾帧槽位的反例**：它没有 `end_image` 这个 input，首尾帧是以「控制视频 + 掩码」的形式编码的，输出还要经 `TrimVideoLatent.trim_amount` 裁掉参考帧。纯靠 input 名匹配无法识别这种首尾帧 workflow。

### 2.9 Hunyuan Video 1.0 文生视频 —— `hunyuan_video_text_to_video.json`

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正提示词 | `CLIPTextEncode` | `text` | CLIP Text Encode (Prompt) | 标题 **CLIP Text Encode (Positive Prompt)** |
| 负提示词 | —— | —— | —— | **不存在**。走 `FluxGuidance` + `BasicGuider` 的无 CFG 路径 |
| 宽 / 高 / 帧数 | `EmptyHunyuanLatentVideo` | `width` / `height` / `length` | Empty HunyuanVideo 1.0 Latent | 848 × 480，73 帧 |
| 种子 | `RandomNoise` | `noise_seed` | —— | 值为 1 |
| fps | `CreateVideo` | `fps` | Create Video | 24 |
| 产物 | `SaveVideo` | `filename_prefix` | Save Video | `video/ComfyUI` |

采样器是 `SamplerCustomAdvanced`（`noise` / `guider` / `sampler` / `sigmas` / `latent_image`），**没有 `positive` / `negative` 端口**。

### 2.10 Hunyuan Video 1.5 720p 图生视频 —— `video_hunyuan_video_1.5_720p_i2v.json`

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正 / 负提示词 | `CLIPTextEncode` ×2 | `text` | CLIP Text Encode (Prompt) | 负向内容是空串 |
| 首帧 | `LoadImage` → `HunyuanVideo15ImageToVideo` | `image` → `start_image` | Load Image | 同图另接 `CLIPVisionEncode` |
| 宽 / 高 / 帧数 | `HunyuanVideo15ImageToVideo` | `width` / `height` / `length` | —— | 1280 × 720，121 帧 |
| 二阶超分 | `HunyuanVideo15SuperResolution` | `start_image` / `positive` / `negative` | Hunyuan Video 1.5 Super Resolution | 第二段也吃 `start_image` |
| 超分尺寸 | `HunyuanVideo15LatentUpscaleWithModel` | `width` / `height` | —— | 1920 × 1080 |
| 种子 | `RandomNoise` ×2 | `noise_seed` | —— | 887963123424675 / 889 |
| fps | `CreateVideo` ×2 | `fps` | Create Video | 均 24 |
| 产物 | `SaveVideo` ×2 | `filename_prefix` | Save Video | `video/hunyuan_video_1.5`（codec h264）+ `video/hunyuan_video_1.5_sr` |

**两个 `SaveVideo` 分别是基础版与超分版**，真正的最终产物是超分那个。

### 2.11 LTX-Video 文生视频 —— `ltxv_text_to_video.json`

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正 / 负提示词 | `CLIPTextEncode` ×2 | `text` | CLIP Text Encode (Prompt) | 标题带 Positive / Negative |
| 宽 / 高 / 帧数 | `EmptyLTXVLatentVideo` | `width` / `height` / `length` | —— | 768 × 512，97 帧（`length` 的 step 是 **8**，不是 4） |
| 模型侧帧率 | `LTXVConditioning` | `frame_rate` | —— | **25.0**，是 CONDITIONING 上的字段 |
| 种子 | `SamplerCustom` | `noise_seed` | —— | —— |
| fps | `CreateVideo` | `fps` | Create Video | **24**，与 `frame_rate` 不一致 |
| 产物 | `SaveVideo` | `filename_prefix` | Save Video | —— |

### 2.12 LTX-Video 图生视频 —— `ltxv_image_to_video.json`

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正 / 负提示词 | `CLIPTextEncode` ×2 | `text` | CLIP Text Encode (Prompt) | —— |
| 首帧 | `LoadImage` → `LTXVImgToVideo` | `image` → **`image`** | Load Image | 不叫 `start_image` |
| 宽 / 高 / 帧数 | `LTXVImgToVideo` | `width` / `height` / `length` | —— | 768 × 512，97 帧；另有 `strength`=0.15 |
| 模型侧帧率 | `LTXVConditioning` | `frame_rate` | —— | 25.0 |
| 种子 | `SamplerCustom` | `noise_seed` | —— | —— |
| fps | `CreateVideo` | `fps` | Create Video | 24 |
| 产物 | `SaveVideo` | `filename_prefix` | Save Video | —— |

### 2.13 Flux.1 dev 文生图 —— `flux_dev_full_text_to_image.json`（subgraph）

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正提示词 | `CLIPTextEncode` | `text` | CLIP Text Encode (Prompt) | 标题为空；`text` 提升为端口 `text` |
| 负提示词 | `ConditioningZeroOut` | `conditioning` | —— | **没有负向文本节点**，直接把正向条件清零 |
| 宽 / 高 | `EmptySD3LatentImage` | `width` / `height` | —— | 1024 × 1024，提升为端口 |
| 种子 | `KSampler` | `seed` | KSampler | 提升为端口 `seed` |
| 产物 | `SaveImage` | `filename_prefix` | Save Image | `ComfyUI` |

### 2.14 Flux.1 Kontext dev 图像编辑 —— `flux_kontext_dev_basic.json`（subgraph）

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 指令提示词 | `CLIPTextEncode` | `text` | CLIP Text Encode (Prompt) | 标题 **CLIP Text Encode (Positive Prompt)** |
| 负提示词 | `ConditioningZeroOut` | `conditioning` | —— | 无负向文本 |
| 参考图 1 / 2 | `LoadImage` ×2 → `ImageStitch` | `image` → `image1` / `image2` | Load Image | 两图横向拼接（`direction=right`） |
| 尺寸 | **无** | —— | —— | `FluxKontextImageScale` 从拼接后的图推导，无任何 `width`/`height` |
| 参考潜变量 | `ReferenceLatent` | `latent` | Set Reference Latent | —— |
| 引导强度 | `FluxGuidance` | `guidance` | —— | 2.5 |
| 种子 | `KSampler` | `seed` | KSampler | 字面量，未提升 |
| 产物 | `SaveImage` | `filename_prefix` | Save Image | `flux.1_kontext_dev` |

### 2.15 SDXL 基础文生图 —— `image_sdxl_simple.json`

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正提示词 | `CLIPTextEncode` | `text` | CLIP Text Encode (Prompt) | 标题 **Positive Prompt**（不含 "CLIP Text Encode"） |
| 负提示词 | `CLIPTextEncode` | `text` | 同上 | 标题 **Negative Prompt** |
| 宽 / 高 | `EmptyLatentImage` | `width` / `height` | Empty Latent Image | 1024 × 1024，标题 **Empty Latent** |
| 种子 | `KSampler` | `seed` | KSampler | 812045847300606 |
| 产物 | `SaveImage` | `filename_prefix` | Save Image | `sdxl_simple` |

### 2.16 SDXL base + refiner —— `sdxl_simple_example.json`

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正提示词 | `CLIPTextEncode` ×2 | `text` | CLIP Text Encode (Prompt) | base 一个、refiner 一个，**标题全为空** |
| 负提示词 | `CLIPTextEncode` ×2 | `text` | 同上 | 同上 |
| 文本真源 | `PrimitiveNode` | —— | 标题 **Positive Prompt (Text)** / **Negative Prompt (Text)** | 4 个 `CLIPTextEncode` 的 `text` 都由这两个 Primitive 驱动 |
| 宽 / 高 | `EmptyLatentImage` | `width` / `height` | Empty Latent Image | 1024 × 1024 |
| 种子 | `KSamplerAdvanced` ×2 | `noise_seed` | KSampler (Advanced) | base 721897303308196；refiner 0 且 `add_noise=disable` |
| 产物 | `SaveImage` | `filename_prefix` | Save Image | `ComfyUI` |

**这是最难推断的样本**：4 个同类提示词节点、标题全空、真正可编辑的文本在游离的 `PrimitiveNode` 上。注意 `PrimitiveNode` 是旧式前端虚拟节点，导出 API 格式时会被折叠进下游节点的字面量，所以 API 格式里只会看到 4 个各自带 `text` 的 `CLIPTextEncode`。

### 2.17 Qwen-Image 文生图 —— `image_qwen_image.json`（subgraph）

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正提示词 | `CLIPTextEncode` | `text` | CLIP Text Encode (Prompt) | 标题 **CLIP Text Encode (Positive Prompt)**，提升为端口 `text` |
| 负提示词 | `CLIPTextEncode` | `text` | 同上 | 标题 **…(Negative Prompt)**，内容为空串，未提升 |
| 宽 / 高 | `EmptySD3LatentImage` | `width` / `height` | —— | 1328 × 1328，提升为端口 |
| 种子 | `KSampler` | `seed` | KSampler | 提升为端口 `seed` |
| 产物 | `SaveImage` | `filename_prefix` | Save Image | `Qwen-Image` |

模板还用 `ComfySwitchNode` + `PrimitiveBoolean`（标题 **Enable Lightning LoRA**）在「8 步 LoRA」与「20 步」两套 steps/cfg 之间切换，`KSampler.steps` 和 `.cfg` 都是连线而非字面量。

### 2.18 Qwen-Image-Edit 2509 —— `image_qwen_image_edit_2509.json`（subgraph）

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正（指令）提示词 | `TextEncodeQwenImageEditPlus` | **`prompt`** | —— | 不叫 `text` |
| 负提示词 | `TextEncodeQwenImageEditPlus` | **`prompt`** | —— | 第二个同类节点，内容为空串 |
| 参考图 1 | `LoadImage`（顶层）→ `FluxKontextImageScale` → 两个编码节点 | `image` → `image1` | Load Image | —— |
| 参考图 2 / 3 | `TextEncodeQwenImageEditPlus` | `image2` / `image3` | —— | 提升为端口，未连接 |
| 尺寸 | **无** | —— | —— | 由 `FluxKontextImageScale` 从输入图推导 |
| 种子 | `KSampler` | `seed` | KSampler | 提升为端口 `seed_1` |
| 产物 | `SaveImageAdvanced` | `filename_prefix` | Save Image (Advanced) | `Qwen_Image_2509`，`format=png`，`bit_depth=8-bit`，`input_color_space=sRGB` |

`TextEncodeQwenImageEditPlus` 的 input：`clip, prompt, vae?, image1?, image2?, image3?`（`nodes_qwen.py`）。单图版 `TextEncodeQwenImageEdit` 是 `clip, prompt, vae?, image?`。

### 2.19 SDXL → SVD 两段式 —— `txt_to_image_to_video.json`

| 槽位 | class_type | input 名 | 默认标题 | 备注 |
|---|---|---|---|---|
| 正 / 负提示词 | `CLIPTextEncode` ×2 | `text` | CLIP Text Encode (Prompt) | 只服务于 SDXL 图像阶段；SVD 阶段无文本条件 |
| 图像阶段尺寸 | `EmptyLatentImage` | `width` / `height` | Empty Latent Image | 1024 × 576 |
| 视频阶段尺寸 | `SVD_img2vid_Conditioning` | `width` / `height` | —— | 1024 × 576 |
| 帧数 | `SVD_img2vid_Conditioning` | **`video_frames`** | —— | 25。**全样本唯一不叫 `length` 的帧数字段** |
| 模型侧 fps | `SVD_img2vid_Conditioning` | `fps` | —— | 6 |
| 首帧 | `VAEDecode` → `SVD_img2vid_Conditioning` | `init_image` | —— | 由上一阶段生成，不是 `LoadImage` |
| 种子 | `KSampler` ×2 | `seed` | KSampler | 图像阶段与视频阶段各一 |
| 输出 fps | `CreateVideo` | `fps` | Create Video | **10**，与模型侧 `fps=6` 不一致 |
| 产物 | `SaveVideo` + `PreviewImage` | —— | Save Video / Preview Image | `PreviewImage` 是中间图预览，不是最终产物 |

---

## 3. 社区 workflow：Kijai WanVideoWrapper + VHS_VideoCombine

样本取自 [`kijai/ComfyUI-WanVideoWrapper`](https://github.com/kijai/ComfyUI-WanVideoWrapper) 的 `example_workflows/`（抓取于 2026-09-17）：`wanvideo_2_1_14B_T2V_example_03.json`、`wanvideo_2_1_14B_I2V_example_03.json`、`wanvideo_2_1_14B_FLF2V_720P_example_02.json`。节点定义取自该仓的 `nodes.py` / `nodes_sampler.py`，以及 [`Kosinkadink/ComfyUI-VideoHelperSuite`](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite) 的 `videohelpersuite/nodes.py`。

**这组样本的价值在于它几乎每一条都和官方模板相反**，是自动推断规则最好的压力测试。

### 3.1 三个样本的槽位落点

| 槽位 | class_type | input 名 | 备注 |
|---|---|---|---|
| **正 + 负提示词** | `WanVideoTextEncode` | `positive_prompt` **和** `negative_prompt` | **一个节点同时持有正负两段文本** |
| 宽 / 高 / 帧数（T2V） | `WanVideoEmptyEmbeds` | `width` / `height` / **`num_frames`** | 832 × 480 × 81 |
| 宽 / 高 / 帧数（I2V、FLF2V） | `WanVideoImageToVideoEncode` | `width` / `height` / **`num_frames`** | 同上；`num_frames` 的 step 也是 4 |
| 首帧 | `WanVideoImageToVideoEncode` | `start_image` | 与官方同名 |
| 尾帧 | `WanVideoImageToVideoEncode` | `end_image` | 与官方同名 |
| 种子 | `WanVideoSampler` | `seed` | —— |
| fps + 产物 | `VHS_VideoCombine` | `frame_rate` + `filename_prefix` + `format` | **fps 和产物在同一个节点上** |

FLF2V 样本里两个 `LoadImage` 的标题是 **Load Image: Start image** 与 **Load Image: End image**，但中间隔着 `SetNode` / `GetNode`（KJNodes 的变量传递节点）和 `ImageResizeKJv2`，链路比官方模板深得多。

### 3.2 `VHS_VideoCombine` 节点定义

`videohelpersuite/nodes.py` 的 `class VideoCombine`，注册名 `VHS_VideoCombine`，display name `Video Combine 🎥🅥🅗🅢`：

| 类别 | input 名 | 类型 | 默认值 |
|---|---|---|---|
| required | `images` | IMAGE 或 LATENT（`MultiInput`） | —— |
| required | `frame_rate` | **FLOAT 或 INT**（`MultiInput`） | **8**，min 1，step 1 |
| required | `loop_count` | INT | 0 |
| required | `filename_prefix` | STRING | `AnimateDiff` |
| required | `format` | COMBO | `image/gif`、`image/webp` + `video_formats/` 目录下的全部条目 |
| required | `pingpong` | BOOLEAN | False |
| required | `save_output` | BOOLEAN | True |
| optional | `audio` | AUDIO | —— |
| optional | `meta_batch` | VHS_BatchManager | —— |
| optional | `vae` | VAE | —— |

`RETURN_TYPES = ("VHS_FILENAMES",)`，`RETURN_NAMES = ("Filenames",)`，`OUTPUT_NODE = True`，`CATEGORY = "Video Helper Suite 🎥🅥🅗🅢"`。

`format` 的 ffmpeg 取值来自 `video_formats/` 目录的 13 个 JSON：`8bit-png`、`16bit-png`、`ProRes`、`av1-webm`、`ffmpeg-gif`、`ffv1-mkv`、`gifski`、`h264-mp4`、`h265-mp4`、`nvenc_av1-mp4`、`nvenc_h264-mp4`、`nvenc_hevc-mp4`、`webm`，在图里表现为 `video/h264-mp4` 这样的前缀形式。三个样本全部取 `video/h264-mp4`。

**两个对 ArcReel 直接有影响的坑：**

1. **`format` 是动态 combo**，选中某个格式会追加该格式专属的 widget（`pix_fmt`、`crf`、`lossless` 等）。三个样本的 `widgets_values` 里确实多出了 `pix_fmt: "yuv420p"`、`crf: 19`、`save_metadata`、`trim_to_audio`。API 格式里这些会变成 `inputs` 的额外键。**推断规则不能假设 `inputs` 的键集合等于 `INPUT_TYPES` 的键集合。**
2. **`VHS_VideoCombine` 的 `widgets_values` 在 save 格式里是一个对象而不是数组**：

```json
"widgets_values": {"frame_rate": 16, "loop_count": 0, "filename_prefix": "WanVideo2_1_T2V",
                   "format": "video/h264-mp4", "pix_fmt": "yuv420p", "crf": 19,
                   "save_metadata": true, "pingpong": false, "save_output": true,
                   "videopreview": {...}}
```

   核心节点全是数组。这是「不要解析 save 格式」的又一条证据。注意里面还有个 `videopreview` 键，**它是纯 UI 状态、不是真实 input**，不会出现在 API 格式里。

`save_output: false` 表示只预览不落盘 —— FLF2V 样本正是如此。**判定产物节点时必须检查 `save_output`**，否则会把一个纯预览节点当成最终产物。

### 3.3 相关的 VHS 输入节点

| 注册名 | 关键 input |
|---|---|
| `VHS_LoadVideo` | `video`、`force_rate`、`custom_width`、`custom_height`、`frame_load_cap`、`skip_first_frames`、`select_every_nth`，optional `meta_batch` / `vae` / `format` |
| `VHS_LoadVideoPath` | 同上，首个 input 是路径字符串 |
| `VHS_LoadImages` / `VHS_LoadImagesPath` | 目录批量读图 |
| `VHS_VideoInfo` | 输出 `source_fps` / `source_frame_count` / `source_duration` / `source_width` / `source_height` 及对应的 `loaded_*` |

`VHS_LoadVideo` 用 `custom_width` / `custom_height` 而不是 `width` / `height`，且 `0` 表示「不改变」。

### 3.4 这组样本推翻了哪些假设

| 官方样本给出的印象 | 社区样本的反例 |
|---|---|
| 帧数叫 `length` | `WanVideoEmptyEmbeds.num_frames` / `WanVideoImageToVideoEncode.num_frames` |
| 正负提示词是两个节点 | `WanVideoTextEncode` 一个节点两个字段 |
| 可从采样器 `positive` / `negative` 端口追溯 | `WanVideoSampler` **没有这两个端口**，只有一个 `text_embeds` 打包输入 |
| fps 在 `CreateVideo`、产物在 `SaveVideo` | `VHS_VideoCombine` 一个节点兼任两者，字段叫 `frame_rate` |
| `widgets_values` 是数组 | `VHS_VideoCombine` 的是对象 |
| 提示词节点是 `CLIPTextEncode` | 三个样本里的 `CLIPTextEncode` 是**旁路的备选分支**，主链路走 `WanVideoTextEncode` |

---

## 4. 汇总：每个槽位的 input 名别名分布

计数口径：**19 个官方样本**展平后，**按节点实例出现次数**统计（同一样本内同类节点重复会被多次计入）。

### 4.1 提示词

| input 名 | 承载 class_type | 实例数 | 说明 |
|---|---|---|---|
| `text` | `CLIPTextEncode` | 37 | 绝对主流 |
| `prompt` | `TextEncodeQwenImageEditPlus` | 2 | Qwen 编辑系列；`TextEncodeQwenImageEdit` 同名 |
| `positive_prompt` + `negative_prompt` | `WanVideoTextEncode`（社区） | 3 | 一个节点两个字段，见 §3 |

**负提示词的缺席情况**（19 个官方样本）：

| 形态 | 样本数 | 样本 |
|---|---|---|
| 有独立的负向文本节点 | 15 | Wan 全系、Hunyuan 1.5、LTX ×2、SDXL ×2、Qwen ×2、SVD |
| 用 `ConditioningZeroOut` 代替 | 2 | Flux dev、Flux Kontext |
| 完全没有负向路径 | 1 | Hunyuan Video 1.0 t2v（`BasicGuider` 无 CFG） |
| 负向节点存在但内容为空串 | 3 | Hunyuan 1.5、Qwen-Image、Qwen-Image-Edit 2509 |

### 4.2 图像输入

| input 名 | 承载 class_type | 实例数 | 语义 |
|---|---|---|---|
| `start_image` | `WanImageToVideo`、`WanFirstLastFrameToVideo`、`Wan22ImageToVideoLatent`、`HunyuanVideo15ImageToVideo`、`HunyuanVideo15SuperResolution` | 8 | 首帧 |
| `end_image` | `WanFirstLastFrameToVideo` | 3 | 尾帧 |
| `image` | `LTXVImgToVideo` | 1 | 首帧（LTX 不用 `start_image`） |
| `init_image` | `SVD_img2vid_Conditioning` | 1 | 首帧 |
| `image1` / `image2` / `image3` | `ImageStitch`、`TextEncodeQwenImageEditPlus` | 5 | 参考图 / 编辑底图 |
| `reference_image` | `WanVaceToVideo` | 1 | 参考图 |
| `control_video` | `WanVaceToVideo` | 1 | VACE 用它承载首尾帧 |
| `image` | `LoadImage` | 16 | **文件名**，不是图像连线；是所有图像的源头 |

`LoadImage` 的 input 只有一个 `image`（文件名字符串，`image_upload: True`），语义完全由**它连到了谁的哪个端口**决定。

### 4.3 尺寸

19 个官方样本里所有带尺寸的节点，字段名 **100% 是 `width` / `height`**，无一例外；3 个社区样本同样如此（`WanVideoEmptyEmbeds` / `WanVideoImageToVideoEncode`）。

| 落点 class_type | 实例数 | 默认值（源码） | 备注 |
|---|---|---|---|
| `EmptyLatentImage` | 3 | 512 × 512，step 8 | SD1.5 / SDXL 系 |
| `EmptySD3LatentImage` | 2 | 1024 × 1024，step 16 | Flux / Qwen-Image |
| `EmptyHunyuanLatentVideo` | 3 | 848 × 480，step 16 | Hunyuan 1.0 **与 Wan 2.1/2.2 t2v** |
| `EmptyLTXVLatentVideo` | 1 | 768 × 512，step 32 | LTX |
| `WanImageToVideo` | 2 | 832 × 480，step 16 | 模型专属条件节点 |
| `WanFirstLastFrameToVideo` | 3 | 832 × 480，step 16 | 同上 |
| `WanVaceToVideo` | 1 | 832 × 480，step 16 | 同上 |
| `Wan22ImageToVideoLatent` | 1 | 1280 × 704，step 32 | 同上 |
| `LTXVImgToVideo` | 1 | 768 × 512，step 32 | 同上 |
| `HunyuanVideo15ImageToVideo` | 1 | 848 × 480，step 16 | 同上 |
| `SVD_img2vid_Conditioning` | 1 | 1024 × 576，step 8 | 同上 |
| **无尺寸字段** | **2 个样本** | —— | Flux Kontext、Qwen-Image-Edit 2509，由 `FluxKontextImageScale` 推导 |

分布结论：**空 latent 节点占 9 个实例，模型专属条件节点占 10 个实例**，两类几乎平分。缩放类节点（`ImageScale` 等）在本次官方样本中没有承担尺寸槽位；`FluxKontextImageScale` 是**无参**节点（只有 `image` 一个 input），不能作为尺寸槽位。

### 4.4 视频长度与 fps

| 语义 | input 名 | 承载 class_type | 实例数 |
|---|---|---|---|
| 帧数 | `length` | `EmptyHunyuanLatentVideo`、`EmptyLTXVLatentVideo`、`WanImageToVideo`、`WanFirstLastFrameToVideo`、`WanVaceToVideo`、`Wan22ImageToVideoLatent`、`LTXVImgToVideo`、`HunyuanVideo15ImageToVideo` | 12 |
| 帧数 | `video_frames` | `SVD_img2vid_Conditioning` | 1 |
| 帧数 | `num_frames` | `WanVideoEmptyEmbeds`、`WanVideoImageToVideoEncode`（社区） | 3 |
| 输出帧率 | `fps` | `CreateVideo` | 15 |
| 模型侧帧率 | `frame_rate` | `LTXVConditioning` | 2 |
| 模型侧帧率 | `fps` | `SVD_img2vid_Conditioning` | 1 |
| 输出帧率 | `frame_rate` | `VHS_VideoCombine`（社区） | 3 |

官方模板与 ComfyUI 主仓的相关节点里**只用 `length`**，`num_frames` 完全不出现（`SaveAnimatedWEBP` 源码里有一行被注释掉的 `num_frames`，未启用）。但**社区生态普遍用 `num_frames`**：Kijai WanVideoWrapper 的 `WanVideoEmptyEmbeds` 与 `WanVideoImageToVideoEncode` 都是（见 §3）。`frames` 这个名字在两组样本里都零命中。所以别名表必须同时收 `length`、`num_frames` 和 `video_frames`。

### 4.5 种子

| input 名 | 承载 class_type | 实例数 |
|---|---|---|
| `noise_seed` | `KSamplerAdvanced`（10）、`RandomNoise`（3）、`SamplerCustom`（2） | 15 |
| `seed` | `KSampler`；`WanVideoSampler`（社区，3） | 15 |

**两个名字几乎平分（各 15 个实例），`noise_seed` 不是少数派。** 按样本计：15 个样本只用 `seed`（12 官方 + 3 社区），7 个官方样本含 `noise_seed`。核心节点的这两个字段都带 `control_after_generate: True`；社区的 `WanVideoSampler.seed` 没有该标记。

### 4.6 产物节点与输出格式

| class_type | 实例数 | 关键 input | 输出格式 |
|---|---|---|---|
| `SaveVideo` | 15 | `video`、`filename_prefix`、`format`、`codec` | `format` ∈ {auto, mp4, mkv, webm}；`codec` ∈ {auto, h264, av1}。默认前缀 `video/ComfyUI` |
| `CreateVideo` | 15 | `images`、`fps`、`audio?`、`bit_depth?`、`color_space?`、`codec?` | 输出 VIDEO 类型，不落盘 |
| `SaveImage` | 5 | `images`、`filename_prefix` | PNG，默认前缀 `ComfyUI` |
| `SaveImageAdvanced` | 1 | `images`、`filename_prefix`、`format`、`bit_depth`、`input_color_space` | `format` ∈ {png, exr, avif, …} |
| `PreviewImage` | 1 | `images` | 不落盘，是中间预览 |
| `SaveAnimatedWEBP` | **0** | `images`、`filename_prefix`、`fps`(6.0)、`lossless`、`quality`、`method` | 官方模板已全面迁走 |
| `SaveWEBM` | **0** | `images`、`filename_prefix`、`codec`、`fps`(24.0)、`crf` | 标记为 experimental |
| `VHS_VideoCombine`（社区） | 3 | `images`、**`frame_rate`**、`filename_prefix`、`format`、`save_output`、`loop_count`、`pingpong` | `format` ∈ {image/gif, image/webp, video/h264-mp4, …}；**兼任 fps 与产物** |

**`CreateVideo` + `SaveVideo` 成对出现是当前官方视频模板的唯一形态**（15/15）。`CreateVideo` 是唯一持有输出 fps 的节点，`SaveVideo` 只负责容器与编码，**`SaveVideo` 自身没有 fps 字段**。

---

## 5. 同类节点重复：频率与区分办法

### 5.1 频率

19 个官方样本中 **8 个**（42%）含有两个或以上的 `CLIPTextEncode` / `TextEncodeQwenImageEditPlus` 之外还存在**同类节点重复承担同一槽位**的情况：

| 样本 | 重复形态 | 原因 |
|---|---|---|
| `sdxl_simple_example` | `CLIPTextEncode` ×4、`KSamplerAdvanced` ×2 | base + refiner 两段 |
| `video_wan2_2_14B_flf2v` | `CLIPTextEncode` ×4、`LoadImage` ×4、`WanFirstLastFrameToVideo` ×2、`SaveVideo` ×2 | 加速版 + 完整版两条并行分支 |
| `video_wan2_2_14B_t2v` / `_i2v` | `KSamplerAdvanced` ×2 | Wan 2.2 MoE 的高噪 / 低噪两段 |
| `video_hunyuan_video_1.5_720p_i2v` | `SamplerCustomAdvanced` ×3、`RandomNoise` ×2、`CreateVideo`/`SaveVideo` ×2 | 基础 + 超分两阶段 |
| `txt_to_image_to_video` | `KSampler` ×2、尺寸节点 ×2 | 文生图 + 图生视频两阶段 |
| `image_qwen_image_edit_2509` | `TextEncodeQwenImageEditPlus` ×2 | 正 / 负各一个 |
| `video_wan_vace_flf2v` | `LoadImage` ×2 | 首帧 / 尾帧 |
| `flux_kontext_dev_basic` | `LoadImage` ×2 | 两张参考图 |

「正负各一个 `CLIPTextEncode`」本身是 13 个样本的常态，需要区分正负。

### 5.2 区分办法（按可靠度排序）

**办法 1：从采样器的 `positive` / `negative` 端口反向追溯。** 最可靠。`KSampler` / `KSamplerAdvanced` / `SamplerCustom` / `CFGGuider` 都有具名的 `positive` 和 `negative` 两个 input，API 格式里就是 `"positive": ["6", 0]` 这样的引用。样本覆盖率：

| 采样器 | 有 positive/negative 端口 | 实例数 |
|---|---|---|
| `KSampler` | 是 | 12 |
| `KSamplerAdvanced` | 是 | 10 |
| `SamplerCustom` | 是 | 2 |
| `CFGGuider` | 是 | 3（Hunyuan 1.5） |
| `SamplerCustomAdvanced` | **否**（只有 `guider`） | 4 |
| `BasicGuider` | **否**（只有 `conditioning`） | 1 |

**在官方样本里覆盖率约 87%**。剩下的 `SamplerCustomAdvanced` + `BasicGuider` 组合（Hunyuan 1.0 t2v）压根没有负向路径，所以「追溯失败」在这里恰好等于正确答案：唯一那个 `CLIPTextEncode` 就是正提示词。

**但这条规则在社区 workflow 上会直接失效。** Kijai WanVideoWrapper 的 `WanVideoSampler` 没有 `positive` / `negative` 端口，只有一个打包的 `text_embeds` 输入；正负两段文本都在上游 `WanVideoTextEncode` 的 `positive_prompt` / `negative_prompt` 两个字段里（§3）。这类形态只能靠**字段名本身**（`positive_prompt` / `negative_prompt` 含 positive/negative 子串）来判定。因此字段名匹配不是端口追溯的兜底，而是**并列的第二条主判据**。

**办法 1 的必要扩展：允许多跳追溯。** 正 / 负条件在到达采样器前常常经过中间节点，必须沿链上溯：

| 中间节点 | 透传的端口 | 出现于 |
|---|---|---|
| `WanImageToVideo` / `WanFirstLastFrameToVideo` / `WanVaceToVideo` | `positive` / `negative` 入，`positive` / `negative` 出（槽位 0 / 1） | Wan 全系 |
| `LTXVImgToVideo` / `LTXVConditioning` | 同上 | LTX |
| `HunyuanVideo15ImageToVideo` / `HunyuanVideo15SuperResolution` | 同上 | Hunyuan 1.5 |
| `SVD_img2vid_Conditioning` | 无文本输入，自己产出 positive/negative | SVD |
| `FluxGuidance` / `ReferenceLatent` / `ConditioningZeroOut` | 单端口 `conditioning` | Flux 系 |

这些节点的输出槽位 0 是 positive、1 是 negative，是稳定约定（`nodes_wan.py`、`nodes_lt.py`、`nodes_hunyuan.py` 的 `outputs=[Conditioning.Output(display_name="positive"), Conditioning.Output(display_name="negative"), Latent.Output(...)]`）。

**办法 2：看 `_meta.title`。** 不可靠但可作为兜底与置信度加权。样本里的实际标题：

| 标题形态 | 样本数 |
|---|---|
| `CLIP Text Encode (Positive Prompt)` / `(Negative Prompt)` | 9 |
| `Positive Prompt` / `Negative Prompt`（不含 "CLIP Text Encode"） | 1 |
| 空标题（API 格式里会填成默认 `CLIP Text Encode (Prompt)`） | 5 |

所以**关键词匹配应当是大小写不敏感的 `negative` / `positive` 子串匹配，而不是整串比对**；且约三分之一的样本标题里根本没有这两个词。

**办法 3：默认标题就是 display name。** 当节点标题为空时，API 格式的 `_meta.title` 会被填成节点的 display name。`CLIPTextEncode` 的 display name 是 `"CLIP Text Encode (Prompt)"`（`nodes.py:2162`），**它同时包含 "Prompt" 却不含 "Positive"/"Negative"**，正负两个节点会拿到完全相同的 `_meta.title`。因此标题在最常见的「两个都没改名」场景下毫无区分力。

**办法 4：位置 / id 顺序。** 官方模板里正向节点的 id 常常小于负向（`#6` vs `#7`），但 `video_wan2_2_14B_flf2v` 里是 `#78`(neg) 早于 `#90`(pos)，`image_qwen_image_edit_2509` 里是 `#110`(neg) 早于 `#111`(pos)。**不可用作判据。**

**办法 5：内容启发。** 负向文本常是空串（3 个样本）或那段固定的中文负向词（Wan 全系 7 个样本用同一段「色调艳丽，过曝，静态…」）。可作为低权重信号。

### 5.3 多分支 / 多阶段如何选

当出现多个 `SaveVideo` / `SaveImage` 时，样本里的语义是：

- `video_wan2_2_14B_flf2v`：两条**互斥的并行分支**，用户手动选一条运行。两个产物等价。
- `video_hunyuan_video_1.5_720p_i2v`：**串行两阶段**，第二个 `SaveVideo` 的上游链路包含第一个阶段的采样器输出，是真正的最终产物。
- `txt_to_image_to_video`：串行两阶段，`PreviewImage` 是中间物，`SaveVideo` 是最终产物。

可用的判据：**优先选依赖深度最大的那个输出节点**；`PreviewImage` 永远排除。并行分支的情况无法自动判定，需要让用户选。

---

## 6. 尺寸字段落点分布（详见 §4.3）

补充三条事实：

1. **空 latent 节点与模型专属条件节点二选一，但不会同时承担。** 当 workflow 用 `WanImageToVideo` 这类节点时，图里不会再有 `EmptyHunyuanLatentVideo`；反之亦然。唯一例外是 `txt_to_image_to_video` 这种两阶段 workflow，`EmptyLatentImage` 服务图像阶段、`SVD_img2vid_Conditioning` 服务视频阶段——这两个尺寸是**不同语义**的。
2. **`Wan22ImageToVideoLatent` 是特例：它输出 LATENT 而不是 conditioning**，所以同时扮演「空 latent 节点」和「图像条件节点」两个角色。
3. **step 约束差异很大**：`EmptyLatentImage` 是 8，Flux/Wan/Hunyuan 系是 16，LTX 与 Wan 2.2 5B 是 32。ArcReel 若要提供尺寸预设，得按节点的 step 对齐，否则前端下发的值会被后端拒绝或静默取整。

---

## 7. 视频长度、fps 与时长换算的稳定性

### 7.1 4n+1 约束成立且普遍

`nodes_wan.py` 里所有 Wan 节点计算 latent 帧数都用同一个式子：

```python
latent = torch.zeros([batch_size, 16, ((length - 1) // 4) + 1, height // 8, width // 8], ...)
```

对应 `length` 输入的 `step=4`、`min=1`。Hunyuan（`nodes_hunyuan.py`，step 4）同理，LTX 是 `step=8`。

抽样验证（13 个视频样本的 `length` 实际值）：

| 样本 | length | fps（`CreateVideo`） | `length ≡ 1 (mod 4)` | `(length-1)/fps` | `length/fps` |
|---|---|---|---|---|---|
| Wan 2.1 t2v / i2v / flf2v | 33 | 16 | 是 | 2.000 | 2.062 |
| Wan 2.2 t2v / i2v / flf2v | 81 | 16 | 是 | 5.000 | 5.062 |
| Wan 2.2 5B ti2v | 121 | 24 | 是 | 5.000 | 5.042 |
| Wan VACE flf2v | 81 | 16 | 是 | 5.000 | 5.062 |
| Hunyuan 1.0 t2v | 73 | 24 | 是 | 3.000 | 3.042 |
| Hunyuan 1.5 i2v | 121 | 24 | 是 | 5.000 | 5.042 |
| LTX t2v / i2v | 97 | 24 | 是（且 ≡1 mod 8） | 4.000 | 4.042 |
| SVD | 25（`video_frames`） | 10 | 是 | 2.400 | 2.500 |

**13/13 满足 4n+1。12/13 的 `(length-1)/fps` 是整数秒，`length/fps` 则一个整数都没有。**

### 7.2 官方模板自己就用 `(duration × fps) + 1`

三个使用 subgraph 的 Wan 模板把「时长」暴露为用户可调端口，内部用 `ComfyMathExpression` 换算成 `length`：

| 模板 | 表达式 | 输入 | 结果 |
|---|---|---|---|
| `video_wan2_2_14B_t2v` | `floor(a * b) + 1` | a = Duration 5，b = FPS 16 | 81 |
| `video_wan2_2_14B_i2v` | `floor (a * b + 1)` | a = Duration 5，b = FPS 16 | 81 |
| `video_wan_vace_flf2v` | `a * 16 + 1` | a = Duration 5（fps 硬编码 16） | 81 |

这是**官方对「时长 ↔ 帧数」关系的直接背书**：`length = duration × fps + 1`，反过来 `duration = (length - 1) / fps`。

### 7.3 换算不稳定的两种情况

**情况 A：模型侧帧率与输出帧率不一致。** 两个样本存在两个不同的帧率字段：

| 样本 | 模型侧 | 输出侧（`CreateVideo.fps`） | 两种时长 |
|---|---|---|---|
| LTX t2v / i2v | `LTXVConditioning.frame_rate` = 25 | 24 | 3.84 s vs 4.00 s |
| SVD | `SVD_img2vid_Conditioning.fps` = 6 | 10 | 4.00 s vs 2.40 s |

模型侧 fps 是**生成条件**（告诉模型按什么帧率运动），输出侧 fps 是**封装帧率**（决定播放速度）。两者官方模板里就不一致。**ArcReel 要的是播放时长，应当取 `CreateVideo.fps`**；但需要意识到改这个值会改变播放速度而不改变内容。

**情况 B：`length` 不是字面量。** 3 个 Wan 2.2 / VACE 模板的 `length` 是 `ComfyMathExpression` 的输出。API 格式里这会表现为 `"length": ["163", 0]` 这样的引用，**读不到数值**。同样，`CreateVideo.fps` 在 Wan 2.2 两个模板里也是连线（指向 `PrimitiveFloat`）。

### 7.4 fps 的默认值汇总（源码）

| 节点 | 字段 | 默认值 |
|---|---|---|
| `CreateVideo` | `fps` | 30.0（min 1.0，max 120.0） |
| `SaveWEBM` | `fps` | 24.0 |
| `SaveAnimatedWEBP` | `fps` | 6.0 |
| `SaveAnimatedPNG` | `fps` | 6.0 |
| `LTXVConditioning` | `frame_rate` | 25.0 |
| `SVD_img2vid_Conditioning` | `fps` | 6（INT） |

注意 `CreateVideo` 的默认 30.0 **与所有官方视频模板的实际取值都不同**（实际是 16 或 24）。不能靠默认值猜 fps。

---

## 8. 对 ArcReel 自动推断规则的直接结论

### 8.1 输入格式

1. **只接受 API 格式，明确拒绝 save 格式**，并在错误提示里告诉用户用 `File → Export Workflow (API)`。理由有三：`widgets_values` 的位置解析会因 `control_after_generate` 系统性错位（§1.3）；save 格式需要自行展平 subgraph（7/19 个官方模板用了 subgraph）；save 格式还含 `PrimitiveNode` 这类前端虚拟节点，导出时才会折叠。
2. 识别输入是否为 API 格式的判据：顶层是对象而非数组，且每个 value 含 `class_type` 键。save 格式的判据是顶层含 `nodes` 数组。

### 8.2 推断主键

3. **以 `inputs` 的字段名为主键做匹配，`class_type` 只用于消歧与打分**，因为 input 名收敛（尺寸 100% 是 `width`/`height`，帧数 12/13 是 `length`）而 class_type 发散（12 种以上）。
4. 建议的字段名别名表（按本次抽样的证据强度）：

| 槽位 | 一级别名（样本证实） | 二级别名（需谨慎） |
|---|---|---|
| 提示词文本 | `text`、`prompt`、`positive_prompt`、`negative_prompt` | —— |
| 首帧 | `start_image`、`init_image` | `image`（仅当节点是 `LTXVImgToVideo` 这类模型条件节点）、`image1` |
| 尾帧 | `end_image` | —— |
| 参考图 | `reference_image`、`image2`、`image3` | `image1` |
| 宽 / 高 | `width` / `height` | —— |
| 帧数 | `length`、`num_frames`、`video_frames` | `frames`（两组样本均零命中） |
| fps | `fps`、`frame_rate` | —— |
| 种子 | `seed`、`noise_seed` | —— |

5. **`image` 这个名字必须按承载节点区分语义**：在 `LoadImage` 上它是文件名字符串；在 `LTXVImgToVideo` 上它是首帧；在 `ImageScale` / `CLIPVisionEncode` 上它是中间图像连线。不能统一当首帧处理。

### 8.3 正负提示词区分

6. **两条并列主判据**：(a) 从采样器 `positive` / `negative` 端口反向多跳追溯，覆盖 §5.2 表里的全部中间节点，并利用「模型条件节点输出槽位 0 = positive、1 = negative」这一稳定约定（官方样本覆盖约 87%）；(b) 直接匹配含 `positive` / `negative` 子串的 **input 名**，用于 `WanVideoTextEncode` 这种「一个节点两个字段」的社区形态，此时采样器根本没有正负端口。两条都命中时以 (a) 为准。
7. **`_meta.title` 只作为次级信号**，用大小写不敏感的 `negative` / `positive` 子串匹配。必须处理「两个节点标题都是默认值 `CLIP Text Encode (Prompt)`」这一最常见情况（5/19 个样本标题为空）。
8. **不要用 node id 顺序判定正负**，样本里有反例。
9. **必须支持「没有负提示词」是合法结果**：3/19 个样本（Flux dev、Flux Kontext、Hunyuan 1.0 t2v）压根没有可编辑的负向文本节点。UI 上应当把负提示词槽位标为「此 workflow 不支持」而不是报错。

### 8.4 尺寸

10. **必须支持「没有尺寸槽位」是合法结果**：2/19 个样本（Flux Kontext、Qwen-Image-Edit 2509）的输出尺寸由输入图推导。
11. 尺寸槽位候选按优先级：模型专属条件节点（`Wan*` / `LTXVImgToVideo` / `HunyuanVideo15*` / `SVD_img2vid_Conditioning`）> 空 latent 节点（`Empty*LatentImage` / `Empty*LatentVideo`）> 图像缩放节点。理由是前者一旦存在，空 latent 节点就不会出现。
12. **两阶段 workflow 会有两组语义不同的 `width`/`height`**（`txt_to_image_to_video`）。绑定时应取**最终产物链路上最靠下游的那一组**。
13. 绑定尺寸槽位时要一并记录该 input 的 `step` 约束（8 / 16 / 32 三档），在下发前对齐，否则会被后端取整或拒绝。

### 8.5 时长

14. **时长换算固定用 `duration = (length - 1) / fps`，反向用 `length = round(duration × fps) + 1`**。这一式子有官方模板的 `ComfyMathExpression` 直接背书，且在 12/13 个样本里得到整数秒。用 `length / fps` 在样本里零命中。
15. 生成 `length` 后**必须按承载节点的 `step` 向下取整到合法网格**：Wan / Hunyuan 是 `length ≡ 1 (mod 4)`，LTX 是 `length ≡ 1 (mod 8)`。
16. **fps 槽位绑 `CreateVideo.fps`，不要绑 `LTXVConditioning.frame_rate` 或 `SVD_img2vid_Conditioning.fps`**。后两者是模型生成条件，改动会改变运动速度而非播放时长；且官方模板里它们与输出 fps 本来就不一致（LTX 25 vs 24，SVD 6 vs 10）。
17. **要处理「槽位值是引用而不是字面量」**：3 个 Wan 模板的 `length`、2 个 Wan 模板的 `CreateVideo.fps` 在 API 格式里是 `["<node_id>", <slot>]`。此时应沿引用上溯到常量节点（`PrimitiveInt` / `PrimitiveFloat`）读取数值；若上溯到 `ComfyMathExpression` 这类计算节点则放弃绑定该槽位并提示用户手动指定。
18. **不要用 `CreateVideo.fps` 的源码默认值 30.0 兜底**，所有官方模板的实际值都不是 30。

### 8.6 产物

19. **产物节点识别顺序**：`SaveVideo`（官方 15 实例）> `VHS_VideoCombine`（社区 3 实例）> `SaveImage` / `SaveImageAdvanced`（6 实例）> `SaveAnimatedWEBP` / `SaveWEBM`（官方样本 0 实例，社区仍在用）。`PreviewImage` 必须排除，它是中间预览。
20. **`SaveVideo` 没有 fps 字段**，输出帧率只在它上游的 `CreateVideo` 上。绑定「产物」和绑定「fps」在官方 workflow 里是两个不同节点，在 `VHS_VideoCombine` 里却是同一个节点的 `frame_rate` 字段。推断规则要允许两个槽位落在同一节点上。
20bis. **`VHS_VideoCombine` 必须检查 `save_output`**：为 `false` 时该节点只做预览、不落盘（FLF2V 社区样本即如此），不应被选为最终产物。
21. **多产物节点的消歧**：优先选依赖深度最大的那个（Hunyuan 1.5 的超分阶段、两段式 workflow 的第二段）。若两个产物的依赖深度相当且链路不相交（`video_wan2_2_14B_flf2v` 的两条并行分支），无法自动判定，应让用户在导入时选择。
22. 输出格式信息在 `SaveVideo.format`（auto / mp4 / mkv / webm）与 `SaveVideo.codec`（auto / h264 / av1）；`SaveImageAdvanced` 用 `format`（png / exr / avif …）+ `bit_depth` + `input_color_space`；`SaveImage` 固定 PNG 无格式字段；`VHS_VideoCombine` 用单个 `format` 字段承载容器与编码（`video/h264-mp4` 这种前缀形式）。
23. **不要假设 `inputs` 的键集合等于节点 `INPUT_TYPES` 的键集合。** `SaveVideo`、`SaveImageAdvanced`、`VHS_VideoCombine` 都用动态 combo：选中某个 `format` 会追加该格式专属的字段（`codec` / `bit_depth` / `pix_fmt` / `crf` / `lossless`）。解析时应按「实际出现的键」处理，未知键原样保留回填。

### 8.7 已知无法自动推断的形态

24. **Wan VACE 的首尾帧**：没有 `start_image` / `end_image`，首尾帧被拼成图像批次送进 `control_video`，还要配 `control_masks` 和下游的 `TrimVideoLatent.trim_amount`。纯靠 input 名匹配必然漏判。要么按 `class_type == "WanVaceToVideo"` 特判，要么在 UI 上让用户手动绑定。
25. **`sdxl_simple_example` 式的多阶段同类节点**：4 个 `CLIPTextEncode` 分属 base 与 refiner，正确做法是把同一语义的多个节点绑到**同一个槽位、一次写入多处**，而不是只绑其中一个。
26. **Wan 2.2 MoE 的双采样器**：种子槽位对应两个 `KSamplerAdvanced` 的 `noise_seed`，但低噪那一档的 `add_noise` 是 `disable`、种子恒为 0。应只绑 `add_noise == "enable"` 的那个。

---

## 9. 附：样本清单

全部取自 `Comfy-Org/workflow_templates` 的 `templates/` 目录（2026-09-17）。

| # | 文件 | 类别 | subgraph |
|---|---|---|---|
| 1 | `text_to_video_wan.json` | Wan 2.1 T2V | 否 |
| 2 | `image_to_video_wan.json` | Wan 2.1 I2V | 否 |
| 3 | `wan2.1_flf2v_720_f16.json` | Wan 2.1 FLF2V | 否 |
| 4 | `video_wan2_2_14B_t2v.json` | Wan 2.2 T2V | 是 |
| 5 | `video_wan2_2_14B_i2v.json` | Wan 2.2 I2V | 是 |
| 6 | `video_wan2_2_14B_flf2v.json` | Wan 2.2 FLF2V | 否 |
| 7 | `video_wan2_2_5B_ti2v.json` | Wan 2.2 5B TI2V | 否 |
| 8 | `video_wan_vace_flf2v.json` | Wan 2.1 VACE FLF2V | 是 |
| 9 | `hunyuan_video_text_to_video.json` | Hunyuan Video 1.0 T2V | 否 |
| 10 | `video_hunyuan_video_1.5_720p_i2v.json` | Hunyuan Video 1.5 I2V | 否 |
| 11 | `ltxv_text_to_video.json` | LTX-Video T2V | 否 |
| 12 | `ltxv_image_to_video.json` | LTX-Video I2V | 否 |
| 13 | `flux_dev_full_text_to_image.json` | Flux.1 dev T2I | 是 |
| 14 | `flux_kontext_dev_basic.json` | Flux.1 Kontext 编辑 | 是 |
| 15 | `image_sdxl_simple.json` | SDXL 基础 | 否 |
| 16 | `sdxl_simple_example.json` | SDXL base+refiner | 否 |
| 17 | `image_qwen_image.json` | Qwen-Image T2I | 是 |
| 18 | `image_qwen_image_edit_2509.json` | Qwen-Image-Edit 2509 | 是 |
| 19 | `txt_to_image_to_video.json` | SDXL → SVD 两段式 | 否 |

社区样本取自 `kijai/ComfyUI-WanVideoWrapper` 的 `example_workflows/`（2026-09-17）：

| # | 文件 | 类别 | 产物节点 |
|---|---|---|---|
| 20 | `wanvideo_2_1_14B_T2V_example_03.json` | WanVideoWrapper T2V | `VHS_VideoCombine`（`save_output=true`） |
| 21 | `wanvideo_2_1_14B_I2V_example_03.json` | WanVideoWrapper I2V | `VHS_VideoCombine`（`save_output=true`） |
| 22 | `wanvideo_2_1_14B_FLF2V_720P_example_02.json` | WanVideoWrapper FLF2V | `VHS_VideoCombine`（`save_output=false`，仅预览） |

