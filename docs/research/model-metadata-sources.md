# 模型元信息的第三方数据源比较

**调研日期**：2026-10-08
**对应议题**：[#3081](https://github.com/ArcReel/ArcReel/issues/3081)（地图 [#3079](https://github.com/ArcReel/ArcReel/issues/3079)）
**问题**：哪些第三方数据源可以作为模型元信息（价格、能力、上下文和输出限额）的同步来源？它们的覆盖面、表达力、许可和可用性各是什么情况？

**方法**：

- 读取各候选的一手材料：仓库源码与 schema、官方 README、API 文档、服务条款。
- 在 2026-10-08 下载各候选的完整数据文件，用脚本统计字段与覆盖面，并与 `lib/config/registry.py` 的 `PROVIDER_REGISTRY` 逐条匹配。
- 抽查 10 个模型，对照厂商官方定价页。

数据文件只用于本次统计，不入库。下文的条目数、命中数都是这一天的快照，几个来源每天都在变。

---

## 1. 结论

1. **没有一个来源能单独满足 ArcReel。**文本模型覆盖得好，图片、视频和 TTS 模型覆盖得差，国内厂商的媒体模型几乎空白。`PROVIDER_REGISTRY` 的 45 个视频条目中，带价格的命中最多只有 10 个（LiteLLM，全部是 Google、OpenAI、xAI）。Vidu、可灵（大部分型号）、火山方舟原生的 Seedream 与 Seedance、Agnes 的媒体模型和 `qwen3-tts-flash`，四个候选里都没有原生条目。
2. **人民币价格只有 basellm 能表达，且只覆盖部分国内文本模型。**models.dev 没有币种概念，国内端点的数字是美元折算值或直接照抄国际价。LiteLLM 和 OpenRouter 全部是美元。
3. **计价结构的表达力 LiteLLM 最强，但字段是扁平的命名约定，不是结构化模型。**缓存写入的 1 小时 TTL、批量、优先级、分时、按秒和按分辨率计价都靠字段名后缀表达。models.dev 的 v1 schema 只有 token 单价和上下文分段，v2 的源码注释把多 TTL 缓存写入和非 token 媒体计价列为待办。
4. **许可上，LiteLLM（MIT）、models.dev（MIT）和 basellm（Apache-2.0）都允许再分发和打包。**OpenRouter 的服务条款禁止抓取或复制站点与服务上的信息，不宜打包进出厂快照。
5. **OpenRouter 的视频模型端点是唯一结构化给出视频时长、分辨率、宽高比和首尾帧能力的来源。**但它的模型 id 是 OpenRouter 自己的命名，价格是 OpenRouter 的售价，SKU 键名也不统一。

**建议**：

- 不把任何一个第三方来源直接作为实例的运行时同步源。ArcReel 定义自己的目录格式，出厂快照和同步下发都用这一份。
- 第三方数据只作为维护侧输入，用来生成差异报告和更新草稿：
  - models.dev：文本模型的主输入。理由是 MIT 许可、按地区和套餐拆分供应商、有 `canonical_model_id`。
  - LiteLLM：交叉校验，并补充海外厂商的媒体价格（Veo、Sora、Grok Imagine、GPT Image）。
  - basellm：国内文本模型的人民币价格参考。
  - OpenRouter：只在人工核对时参考它的视频能力字段，不入快照。
- 国内厂商的图片、视频和 TTS 模型，没有可用的第三方来源，需要 ArcReel 自行维护。
- LiteLLM 的「随包备份 + 远程拉取 + 校验失败回退」实现，可以直接作为「出厂快照 + 同步增强」的参考（见 3.1）。

---

## 2. 候选总览

| 维度 | LiteLLM | models.dev | basellm（llm-metadata） | OpenRouter models API |
|---|---|---|---|---|
| 形态 | 单个静态 JSON | 静态 JSON API，背后是仓库里的 TOML | 静态 JSON API（GitHub Pages / Cloudflare Pages） | REST API |
| 体积 | 3.1 MB（gzip 约 150 KB） | `api.json` 5.4 MB（gzip 约 535 KB） | `all.json` 622 KB | `/models?output_modalities=all` 约 1 MB |
| 条目 | 4,504 个键，138 个 `litellm_provider` | 226 个供应商，8,454 个供应商-模型条目 | 52 个原生供应商，565 个模型 | 665 个模型（默认只返回文本模型 467 个） |
| schema | 有 JSON Schema（draft 2020-12），声明「新字段会持续增加，消费方应忽略未知字段」 | zod schema（v1 生效，v2 开发中） | 沿用 models.dev，加 `currency`、`currency_options`、`schedule` 扩展 | OpenAPI 文档 |
| 版本号 | 无，依赖 git 提交 | 无，HTTP 有 `ETag` | `manifest.json` 有 `version: 1`、`generatedAt` 和各输入的哈希 | 无 |
| 更新频率 | 近一周该文件每天 2～16 次提交 | 每小时自动同步 38 家供应商的模型列表，另有社区 PR | 每天一次定时构建 | 实时 |
| 币种 | 全部美元 | 无币种字段，按美元理解 | 每个 `cost` 一种币种，支持 CNY | 全部美元 |
| 许可 | MIT（`enterprise/` 目录除外） | MIT | Apache-2.0 | 无数据许可；服务条款限制抓取和复制 |
| 是否需要认证 | 否 | 否 | 否 | 文档写需要 Bearer Key；本次匿名请求返回 200 |

来源：

- LiteLLM：[`model_prices_and_context_window.json`](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json)、[`model_prices_and_context_window.schema.json`](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.schema.json)、[`LICENSE`](https://github.com/BerriAI/litellm/blob/main/LICENSE)。
- models.dev：[README](https://github.com/anomalyco/models.dev)、[`packages/core/src/schema.ts`](https://github.com/anomalyco/models.dev/blob/dev/packages/core/src/schema.ts)、[`schema-v2.ts`](https://github.com/anomalyco/models.dev/blob/dev/packages/core/src/schema-v2.ts)、[`sync-models.yml`](https://github.com/anomalyco/models.dev/blob/dev/.github/workflows/sync-models.yml)。仓库已从 `sst/models.dev` 迁到 `anomalyco/models.dev`。
- basellm：[README](https://github.com/basellm/llm-metadata)、[`manifest.json`](https://basellm.github.io/llm-metadata/api/manifest.json)。
- OpenRouter：[List models](https://openrouter.ai/docs/api/api-reference/models/get-models)、[Video generation](https://openrouter.ai/docs/guides/overview/multimodal/video-generation)、[Terms of Service](https://openrouter.ai/terms)。

### 2.1 basellm 具体是什么

「basellm」指 GitHub 组织 [basellm](https://github.com/basellm) 下唯一的公开仓库 [`basellm/llm-metadata`](https://github.com/basellm/llm-metadata)。组织简介说明它由 NewAPI 与 VoAPI 团队共同发起。

`llm-metadata` 不是独立采集的数据源，而是 models.dev 的下游：

- 上游是 `https://models.dev/api.json` 加社区贡献。
- 构建时只保留 `data/native-providers.json` 白名单里的原生（第一方）供应商。本次构建丢弃了 174 个供应商和 89 个模型。
- 在 `data/overrides/**` 里用覆盖文件补人民币价格、分时价格和多币种价目表。
- 额外产出 new-api 和 VoAPI 的计费配置（`ratio_config`、计费表达式）。

它对 ArcReel 的价值在国内厂商的人民币价格：README 明确写了「models.dev 没有币种概念，国内端点的数字是第三方折算值或照抄的国际价」，并用 `cost.currency` 显式建模。构建产物 `manifest.json` 会把仍为美元估算的条目列为警告。本次警告包括 `alibaba-cn` 有 65 个模型仍是美元估算。

### 2.2 调研中纳入的其他来源

| 来源 | 许可 | 要点 | 结论 |
|---|---|---|---|
| [Portkey-AI/models](https://github.com/Portkey-AI/models) | MIT | 按供应商分文件，免认证 API `configs.portkey.ai/pricing/{provider}.json`；有 `currency` 字段和 `additional_units`（按张、按分辨率秒）；有 `byteplus`（火山国际站）但没有 `volcengine` | 可作补充参考。单价单位是「美分/token」，且抽到的 `minimax.json` 中 `MiniMax-M2.7` 的数值按美元填写，与文件内其他条目差 100 倍，质量需逐条核对 |
| [pydantic/genai-prices](https://github.com/pydantic/genai-prices) | MIT | 支持历史价格、分时价格和上下文分段；有 Python、JS、Go 包 | 只覆盖 LLM 推理，没有火山方舟和阿里云百炼，不适合媒体模型 |
| LobeHub `packages/model-bank` | LobeHub Community License | TypeScript 源码形式，国内厂商覆盖广，用 `currency: 'CNY'` 和 `units[].strategy`（`fixed`、`tiered`、`lookup`）表达价格，含缓存写入 TTL 查表 | 许可要求「分发衍生作品需商业授权」，不能打包；计价 schema 设计值得参考 |

---

## 3. 逐项事实

### 3.1 LiteLLM

**数据形态**：顶层是 `{模型键: 条目}` 字典，`sample_spec` 是字段说明样例。条目字段名约 230 种，全部是可选字段，靠命名约定扩展，例如：

- 基础：`input_cost_per_token`、`output_cost_per_token`、`max_input_tokens`、`max_output_tokens`、`mode`、`source`
- 缓存：`cache_read_input_token_cost`、`cache_creation_input_token_cost`、`cache_creation_input_token_cost_above_1hr`
- 上下文分段：`input_cost_per_token_above_200k_tokens`、`..._above_272k_tokens` 等固定阈值后缀，或 `tiered_pricing: [{range: [lo, hi], input_cost_per_token, ...}]`
- 服务档位：`*_batches`、`*_flex`、`*_priority`
- 分时：`off_peak_pricing: {..., windows: [{hours_utc, weekdays}]}`
- 媒体：`output_cost_per_image`、`output_cost_per_image_1K/2K/4K`、`output_cost_per_second`、`output_cost_per_second_720p/1080p/4k`、`output_cost_per_video_per_second`、`input_cost_per_character`、`input_cost_per_image`
- 能力：`supports_vision`、`supports_function_calling`、`supports_response_schema`、`supports_prompt_caching`、`supports_reasoning`、`supported_modalities`、`supported_output_modalities`

**`mode` 分布**：`chat` 3,375，`image_generation` 409，`responses` 171，`embedding` 149，`video_generation` 46，`audio_speech` 42，`image_edit` 31。

**模型标识**：键是 `<provider 前缀>/<模型名>`，OpenAI 和 Anthropic 等常用模型同时有不带前缀的键。条目的 `litellm_provider` 标明路由供应商。同一模型经不同平台时是不同的键，例如 `gemini/...`、`vertex_ai/...`、`openrouter/...`。阿里云的条目分散在 `dashscope`、`qwencloud`、`qwen_ai_platform` 三个前缀下，`dashscope/qwen3-max` 的价格是国际站美元价。

**远程加载机制**（[`get_model_cost_map.py`](https://github.com/BerriAI/litellm/blob/main/litellm/litellm_core_utils/get_model_cost_map.py)）：

- PyPI 包内带 `model_prices_and_context_window_backup.json` 作为出厂备份。
- 启动时默认从 GitHub 拉取最新文件。
- 拉取结果先校验：不是字典、为空、模型数比备份缩水超过阈值时，回退到本地备份。
- 环境变量 `LITELLM_LOCAL_MODEL_COST_MAP=True` 强制只用本地备份。

这与本图「出厂快照随版本发布、同步只是增强」的决策同构。

### 3.2 models.dev

**数据形态**：仓库按 `providers/<provider>/models/<model>.toml` 存放，生成 `api.json`（按供应商嵌套）、`models.json`（与供应商无关的模型事实）、`catalog.json`（两者合并）。

**cost schema（v1）**：

```text
input, output, reasoning?, cache_read?, cache_write?, input_audio?, output_audio?
tiers?: [{ ...同上, tier: { type: "context", size } }]
context_over_200k?   # 生成产物里的兼容字段
```

- 单位固定为「美元/百万 token」，没有币种字段。
- 只有一个 `cache_write`，表达不了 5 分钟与 1 小时两种 TTL。
- 没有按张、按秒、按分辨率、按字符的字段。视频与 TTS 模型的 `cost` 多为空。
- `schema-v2.ts` 的注释写着 v2 仍需支持「service tiers」和「5m vs 1h cache TTLs, non-token media pricing」，尚未实现。
- `experimental.modes` 可以放 OpenAI fast 模式这类附加价格。

**能力字段**：`attachment`、`reasoning`、`reasoning_options`、`tool_call`、`structured_output`、`temperature`、`modalities.input/output`、`limit.context/input/output`、`knowledge`、`release_date`、`status`。

**模型标识**：`<provider id>` + `<该供应商 API 的模型 id>`。地区和套餐拆成独立供应商，例如 `alibaba` 与 `alibaba-cn`、`minimax` 与 `minimax-cn`、`volcengine` 与 `volcengine-coding-plan`、`xiaomi-token-plan-cn`。转售平台的条目用 `base_model` 继承实验室条目，产物里给出 `canonical_model_id`，可以把中转站的模型归到原厂模型。

**更新**：`sync-models.yml` 每小时运行一次，按供应商的模型列表 API 自动开 PR。自动同步的 38 家里没有火山方舟、阿里云百炼、MiniMax、智谱等国内厂商，这些厂商靠人工 PR。

### 3.3 basellm（llm-metadata）

- 数据结构与 models.dev 相同，额外扩展：
  - `cost.currency`：声明该价目表的币种。声明了币种的覆盖文件会整体替换上游 `cost`，而不是深合并。
  - `cost.currency_options.<CODE>`：同一端点的第二套官方价目表，例如 DeepSeek 的人民币价。
  - `cost.schedule`：分时窗口，含 IANA 时区、星期、时段。
  - `per_image`：按张计价。
- `providers.json` 给出每个供应商的 `currency`、`api` 基础地址和 `subscription`（套餐端点）标记。
- 汇率只在生成 new-api 和 VoAPI 产物时使用，固定写在 `native-providers.json`（本次为 `CNY: 7.3`）。JSON API 本身不做换算。
- 每日定时构建，产物提交到仓库的 `dist/` 并发布到 Pages。

### 3.4 OpenRouter

**`GET /api/v1/models`**：

- `pricing` 的值都是字符串，单位是「美元/token」（不是每百万）。字段有 `prompt`、`completion`、`input_cache_read`、`input_cache_write`（多 TTL 时为 5 分钟价）、`input_cache_write_1h`、`internal_reasoning`、`image`、`image_output`、`audio`、`web_search`、`discount`。
- `overrides` 表达条件价格，例如 `min_prompt_tokens: 200000` 时的长上下文价，或 `utc_start`、`utc_end`、`utc_days` 限定的时段价。
- 默认只返回文本输出模型，加 `output_modalities=all` 才返回图片、视频、语音等。
- 列表里的价格是该模型默认端点的价格。`/api/v1/models/{id}/endpoints` 按实际服务商拆开，例如 `google/gemini-3.1-pro-preview` 的 Vertex flex 端点输入价是 $1/M，是标准价的一半。

**`GET /api/v1/videos/models`**（30 个模型）：

- 给出 `supported_durations`、`supported_resolutions`、`supported_aspect_ratios`、`supported_sizes`、`supported_frame_images`、`generate_audio`、`seed`。
- `pricing_skus` 是自由键值，本次出现 40 种键名，例如 `duration_seconds_1080p`、`cents_per_video_output_second_720p`、`video_tokens_4k`、`duration_seconds_with_audio`。有的以美元计，有的以美分计，单位编码在键名里。
- 在 `/api/v1/models` 里这些视频模型的 `pricing` 全是 `"0"`，价格只在视频端点里。

**`GET /api/v1/images/models`**：给出 `supported_parameters`（分辨率、宽高比、张数、参考图数量等），列表里没有价格，价格在各模型的 `endpoints` 详情里。

**模型标识**：`<作者>/<slug>`，例如 `google/veo-3.1`、`bytedance/seedance-2.0`、`kwaivgi/kling-v3.0-pro`。这是 OpenRouter 自己的命名，不等于原厂 API 的模型名，`canonical_slug` 带日期后缀。

**条款**：[Terms of Service](https://openrouter.ai/terms) 第 7 节禁止「use automated tools … to scrape or copy any information on the Site or the Services」，第 12 节声明站点和服务中的 information、data 属于 OpenRouter。条款没有单独说明模型列表和价格能否缓存或再分发。

---

## 4. 覆盖面

### 4.1 与 `PROVIDER_REGISTRY` 的匹配

匹配方法：模型 id 去掉大小写与标点后精确匹配。「命中」包括任意供应商下的同名条目，「带价」指条目里至少有一个非零价格。OpenRouter 的 id 与原厂命名不同，精确匹配会低估它的覆盖，4.2 另做按家族的人工比对。

| 媒体类型（条目数） | LiteLLM 命中/带价 | models.dev 命中/带价 | OpenRouter 命中/带价 | basellm 命中/带价 |
|---|---|---|---|---|
| 文本（39） | 33 / 33 | 38 / 33 | 22 / 22 | 28 / 27 |
| 图片（25） | 9 / 7 | 10 / 5 | 5 / 5 | 10 / 5 |
| 视频（45） | 10 / 10 | 15 / 0 | 4 / 0 | 9 / 0 |
| 音频（1） | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |

按供应商看：

- **Gemini、OpenAI**：文本与图片在四个来源都齐全。Veo 和 Sora 只有 LiteLLM 带价（models.dev 有条目无价）。
- **Grok**：LiteLLM 全部带价，包括 Grok Imagine 视频按分辨率的秒价。models.dev 和 basellm 有视频条目但无价，缺 `grok-imagine-image-pro`。
- **火山方舟**：文本 `doubao-seed-2-0-*` 在 LiteLLM 和 models.dev 有，用的是带日期的 endpoint id，与 ArcReel 一致。Seedream 和 Seedance 在四个来源里都没有 `volcengine` 原生条目，只出现在 Vercel、fal、Runway 或 OpenRouter 的转售条目里。
- **阿里云百炼**：文本覆盖好。`qwen-image-*`、`wan2.7-image*`、`happyhorse-1.1-*` 只以零价套餐条目（`alibaba-token-plan*`）或无价条目出现。`wan2.7` 视频、`happyhorse-1.0-*`、`wan3.0-video`、`qwen3-tts-flash` 都没有。
- **MiniMax**：文本有。LiteLLM 没有 `MiniMax-M2.7` 条目（有 M2、M2.1、M2.5、M3），但有 `speech-02`、`speech-2.6` 的按字符价。`image-01`、Hailuo 和 `S2V-01` 在 LiteLLM、models.dev、basellm 里都没有。
- **可灵、Vidu**：四个来源都没有原生条目。OpenRouter 有 `kwaivgi/kling-v3.0-pro/std` 和 `kling-video-o1`。
- **Agnes**：models.dev 有 `agnes` 供应商，只有 3 个文本模型：`agnes-2.5-pro-alpha` 有价格，`agnes-2.0-flash` 和 `agnes-2.5-flash` 价格为 0。媒体模型没有任何来源覆盖。

### 4.2 OpenRouter 视频端点按家族比对

OpenRouter 视频端点的 30 个模型里，能对上 ArcReel 视频条目的家族有：Veo 3.1（含 Fast、Lite）、Seedance 1.5 Pro / 2.0 / 2.0 Fast / 2.0 Mini / 2.5、Grok Imagine Video（含 1.5、1.5 Lite）、HappyHorse 1.0 / 1.1、Wan 2.7、Wan 3.0、Hailuo 2.3、Kling v3.0、Kling Video O1。

按条目算，`PROVIDER_REGISTRY` 的 45 个视频条目中约 30 个能找到对应家族，但需要一张 id 映射表：

- HappyHorse 的 i2v、t2v、r2v 在 OpenRouter 合并成一个模型。
- Kling v3.0 按 std 和 pro 拆分。

对不上的有：Sora 2、Vidu、Hailuo 2.3 Fast、S2V-01、`kling-v2-5-turbo`、`kling-v2-6`、`kling-v3-omni`、Agnes 视频。`MiniMax-H3` 是否对应 `minimax/hailuo-3`，未查明。

### 4.3 常见 Agent 模型

`deepseek-v4-pro`、`deepseek-v4-flash`、`glm-5.1`、`kimi-k2.6`、`MiniMax-M2.7`、`mimo-v2.5-pro`：

- models.dev 与 basellm 全部有原生条目。
- LiteLLM 缺 `MiniMax-M2.7` 和 `mimo-v2.5-pro` 的原生条目（`xiaomi_mimo` 只有 v2.6 两个模型）。
- OpenRouter 全部有，但价格不是原厂价，见第 6 节。

---

## 5. 计价结构的表达力

| 计价结构 | LiteLLM | models.dev v1 | basellm | OpenRouter |
|---|---|---|---|---|
| 缓存读 | 有 | 有 | 有 | 有 |
| 缓存写 | 有 | 有（单一价格） | 有（单一价格；生成 new-api 表达式时可按倍率补 1 小时价） | 有 |
| 缓存写按 TTL 区分 | `cache_creation_input_token_cost_above_1hr` | 无 | 只在 new-api 产物里按供应商倍率推算 | `input_cache_write_1h` |
| 上下文分段 | 固定阈值后缀或 `tiered_pricing` 区间 | `tiers[].tier.size` | 同 models.dev | `overrides[].min_prompt_tokens` |
| 分时（峰谷） | `off_peak_pricing.windows` | 无 | `schedule.windows` | `overrides[]` 的 `utc_start`、`utc_end`、`utc_days`（本次 9 处） |
| 批量、flex、优先级 | `*_batches`、`*_flex`、`*_priority` | 只能放在 `experimental.modes` | 无 | 按端点拆分，列表只给默认端点 |
| 按张 | `output_cost_per_image`，分档 `_1K/_2K/_4K` 或 `_512/_1024` | 无 | `per_image` | `image_output`（多数是按 token） |
| 按秒、按分辨率 | `output_cost_per_second_<分辨率>` | 无 | 无 | 视频端点 `pricing_skus` |
| 按视频条数 × 时长矩阵 | 无 | 无 | 无 | 部分可用 `duration_seconds_*` 表达 |
| 按字符 | `input_cost_per_character` | 无 | 无 | 用 `prompt` 字段表达，单位语义不明 |
| 币种 | 美元 | 无字段，按美元理解 | `currency`、`currency_options` | 美元 |

国内厂商的人民币价格只有 basellm 提供，覆盖情况如下：

- 已有官方 CNY 覆盖：`volcengine`、`zhipuai`（大部分）、`moonshotai-cn`、`minimax-cn`、`deepseek`（`currency_options.CNY`）。
- 仍为美元估算：`alibaba-cn` 的 65 个模型、`stepfun`、`bailing`、`tencent-tokenhub`。

ArcReel 现有的计价形态里，有几种所有来源都表达不了：

- 按视频条数 × 时长 × 分辨率的矩阵，例如 MiniMax Hailuo 2.3 官方按「768P 6s / 768P 10s / 1080P 6s」每条计价。
- 可灵的积分制。
- 火山方舟 Seedance 的视频 token 计价。

---

## 6. 数据质量抽查

对照官方定价页抽查 10 个模型。✓ 表示与官方一致，✗ 表示不一致，— 表示无条目或无价格。

| 模型 | 官方价格 | LiteLLM | models.dev | basellm | OpenRouter |
|---|---|---|---|---|---|
| Gemini 3.1 Pro Preview | ≤200k：$2 入 / $12 出 / $0.20 缓存；>200k：$4 / $18 / $0.40 [^gemini] | ✓ 含批量、flex、优先级 | ✓ | ✓ | ✓（默认端点） |
| GPT-5.5 | <272K：$5 / $0.50 / $30；>272K：$10 / $1 / $45；另有 Batch、Flex、Fast [^openai] | ✓ 含全部档位 | ✓ 基础与长上下文，Fast 在 `experimental` | ✓ | ✓ |
| Veo 3.1 / Fast / Lite | 每秒 720p、1080p、4k：$0.40/0.40/0.60；$0.10/0.12/0.30；$0.05/0.08/不支持 [^gemini] | ✓ | — | — | ✓（视频端点，另给无音频价） |
| DeepSeek V4 Pro | 峰时 $1.32 / $0.044 / $3.96，非峰时减半；峰时为 UTC 周一至周五 01:00–04:00、06:00–10:00，中国法定节假日除外 [^deepseek] | ✓ 含分时窗口，节假日无法表达 | ✗ 只有非峰时价，峰时低估一半 | ✓ 含分时窗口和 CNY 价目表 | ✗ $0.955 / $1.91，不是原厂价 |
| Kimi K2.6（中国站） | ¥6.50 入 / ¥1.10 缓存命中 / ¥27 出 [^kimi] | 只有国际站美元价 | ✗ `moonshotai-cn` 下填的是美元国际价 | ✓ | ✗ 非原厂价 |
| MiniMax-M2.7（中国站） | ¥2.1 入 / ¥8.4 出 / ¥0.42 缓存读 / ¥2.625 缓存写 [^minimax] | — | ✗ `minimax-cn` 下填的是美元价 | ✓ | ✗ 非原厂价 |
| qwen3-max（北京） | 0–32K：¥2.5 / ¥10；32K–128K：¥4 / ¥16；128K–256K：¥7 / ¥28 [^aliyun] | 国际站美元分段价，最后一段上限写成 252,000 | 分段结构 ✓，数值是人民币折美元 | 同 models.dev，构建产物标为美元估算 | ✗ 非原厂价 |
| GLM-5.1（Z.ai 国际站） | $1.4 / $0.26 缓存 / $4.4 [^zai] | ✓ | ✓（`zhipuai` 下也填了同一组美元价） | ✓；`zhipuai` 另有 ¥6 / ¥24 与 >32K 分段，未能核对 | ✗ 非原厂价 |
| Grok Imagine Video 1.5 | 模型页只写「$0.080 per second」[^xai] | 480p/720p/1080p：$0.08/0.14/0.25 | — | — | 同 LiteLLM，单位为美分 |
| Doubao Seed 2.0 Pro | 官方页为前端渲染，未能抓取 | 美元折算分段价，无缓存价 | 美元折算分段价，含缓存 | ¥3.2 / ¥16，分段 ¥4.8 / ¥24、¥9.6 / ¥48，与 `PROVIDER_REGISTRY` 一致 | — |

[^gemini]: <https://ai.google.dev/gemini-api/docs/pricing>
[^openai]: <https://developers.openai.com/api/docs/pricing>
[^deepseek]: <https://api-docs.deepseek.com/quick_start/pricing>
[^kimi]: <https://platform.kimi.com/docs/pricing/chat>
[^minimax]: <https://platform.minimax.cn/docs/guides/pricing-paygo>
[^aliyun]: <https://help.aliyun.com/zh/model-studio/model-pricing>
[^zai]: <https://docs.z.ai/guides/overview/pricing>
[^xai]: <https://docs.x.ai/developers/models>

观察：

- **海外厂商的美元价**：LiteLLM、models.dev、basellm 三家都准确，LiteLLM 的档位最全。
- **国内厂商**：只有 basellm 的人民币价与官方一致。models.dev 在 `-cn` 供应商下混用折算价和国际价，消费方无法区分。
- **OpenRouter**：列表价是 OpenRouter 路由到的服务商价格，不能当作原厂价。
- **Grok Imagine Video 1.5**：LiteLLM、OpenRouter 和 `PROVIDER_REGISTRY` 的分辨率分档价彼此一致，但官方模型页只看到单一秒价，分档价的出处待确认。

---

## 7. 模型标识与 ArcReel 的映射

ArcReel 用「供应商 id + 模型 id」定位模型，模型 id 就是调用时的 API 模型名。

- **models.dev 与 basellm**：结构上最接近 ArcReel，供应商-模型两级，模型 id 就是该端点的 API 模型名。只需一张供应商 id 映射表：

  | ArcReel | models.dev / basellm |
  |---|---|
  | `gemini-aistudio` | `google` |
  | `gemini-vertex` | `google-vertex` |
  | `ark` | `volcengine` |
  | `ark-agent-plan` | `volcengine-coding-plan` |
  | `grok` | `xai` |
  | `dashscope` | `alibaba-cn` |
  | `minimax` | `minimax-cn` |

  对自定义供应商（中转站），可以用 `canonical_model_id` 把模型归到原厂条目，再取原厂能力。价格仍以中转站为准。
- **LiteLLM**：键前缀与 ArcReel 供应商的对应关系同样需要映射表。缺点是同一前缀可能混合地区，例如 `dashscope/*` 是国际站价。
- **OpenRouter**：id 是 OpenRouter 自己的命名，与原厂 API 名不同（`google/veo-3.1` 对应 `veo-3.1-generate-preview`），需要逐模型维护映射。

---

## 8. 获取与网络

- LiteLLM：GitHub raw 地址和 jsDelivr（`cdn.jsdelivr.net/gh/BerriAI/litellm@main/...`）都能取到。本次 jsDelivr 返回的文件比 raw 小 687 字节，说明 CDN 有缓存滞后。PyPI 的 `litellm` 包内也带一份备份文件。
- models.dev：Cloudflare 托管，`cache-control: public, max-age=0, must-revalidate`，带 `ETag`，适合条件请求。
- basellm：GitHub Pages 与 Cloudflare Pages（`llm-metadata.pages.dev`）双地址。
- OpenRouter：REST API，文档要求 API Key。

国内网络能否稳定访问上述地址，本次没有在中国内地网络环境下实测，待确认。

---

## 9. 对选型的影响

1. **同步源的格式应由 ArcReel 自己定义。**第三方 schema 都表达不全 ArcReel 需要的计价结构：
   - 人民币和美元并存
   - 缓存写的多种 TTL
   - 上下文分段
   - 分时价格
   - 按张、按秒、按分辨率、按条数 × 时长的矩阵
   - 按字符计价

   可借鉴的设计：LiteLLM 的字段覆盖面，basellm 的 `currency` 与 `schedule`，LobeHub 的 `units[].strategy`（`fixed`、`tiered`、`lookup`），OpenRouter 视频端点的能力字段。
2. **分发渠道**：市场源（GitHub 仓库，ADR 0078）与官方服务（`arc-reel.com/api/v1`）都能承载 ArcReel 自有目录。二者的比较见下表；具体选哪个属于地图 #3079 的「官方服务端的模型信息接口」待定项。

   | 渠道 | 优点 | 缺点 |
   |---|---|---|
   | 市场源 | 版本即 git 提交，可审计；不依赖服务端 | 国内访问 GitHub 的稳定性待确认 |
   | 官方服务 | 可以按实例做增量和版本协商 | 需要定义接口契约 |

3. **第三方许可**：
   - 引入 LiteLLM、models.dev、basellm 的数据进出厂快照，按 MIT 和 Apache-2.0 的要求保留版权与许可声明即可。
   - OpenRouter 的数据不入快照。

---

## 10. 风险与未查明项

- **国内网络可达性**：GitHub raw、jsDelivr、`*.github.io`、`*.pages.dev`、models.dev 和 openrouter.ai 在中国内地的可达性，未实测。
- **未能抓取的官方页**：火山方舟定价页、智谱 BigModel 定价页、小米 MiMo 定价页都是前端渲染，未能抓到，相关价格没有核对。
- **Grok Imagine Video 1.5 的分辨率分档价**：没有在官方页面上确认。
- **OpenRouter 匿名访问**：文档要求 Bearer Key，但本次匿名请求成功，这一行为可能随时变化。
- **models.dev v2 schema 在开发中**：字段可能变化；v2 落地后，媒体计价和多 TTL 缓存的表达力可能提升。
- **basellm 依赖维护者**：它是 models.dev 的下游，人民币覆盖取决于覆盖文件的维护进度；汇率是写死的常量。
- **统计口径**：所有统计都是 2026-10-08 的快照，按去标点后的 id 精确匹配，可能漏掉命名不同的同一模型，也可能把同名不同模型算作命中。
