# 主流供应商的实际计价结构与最少计价维度

> 日期：2026-10-08。对应 [#3082](https://github.com/ArcReel/ArcReel/issues/3082)（地图 [#3079](https://github.com/ArcReel/ArcReel/issues/3079)）。
> 问题：ArcReel 涉及的供应商实际用哪些计价结构？现有 10 种 `PricingKind` 是否够用？至少要补哪些计价维度，参考费用才能在结构上算对？
> 方法：只读各厂商官方定价页和计费文档（部分客户端渲染页面经 `r.jina.ai` 读取正文），每条事实附来源 URL。本文只记录计价结构，价格仅作示例；完整价格表不入库（见 `docs/api-docs/AGENTS.md`）。代码对照以 main `c9bf02cd3` 为准。
> 约束：费用定位是参考费用，不对齐充值折扣、套餐包和免费额度（地图 #3079 已定）。

## 结论速览

1. **所有观察到的计价结构都能拆成同一个公式**：费用 =（Σ 计量项用量 × 该计量项单价）× 服务档乘数。差异只在于「有哪些计量项」和「单价由哪些维度选出」。
2. **最少需要 5 组维度**：
   - **计量项**：token（按方向、缓存状态、模态细分）、张数、秒数、字符数、按次、供应商积分。
   - **单价选择维度**：分辨率、质量档、是否有声、输入形态（是否带参考视频等）、时长档、思考模式。
   - **请求级阶梯**：按单次请求输入 token 总量选档，选中的档对整个请求的所有计量项生效。
   - **免费额度（单请求内）**：例如前 N 张参考图免费、首秒另计。
   - **修饰项**：服务档乘数（Batch / Flex / Priority 等）、闲时价、价目表币种。
3. **对照现有 10 种 kind**：
   - 可直接覆盖：`per_character`、`per_video_bucket`。
   - 需扩展：`per_token`（缺缓存读写、缓存存储、请求级阶梯、音频输入、思考模式）、`per_token_video`（选择维度错位）、`per_second_*`（缺输入视频秒数、参考图附加费）。
   - 可合并：`per_image_flat` 并入 `per_image_by_resolution`；`per_second_matrix` 与 `per_second_tiered` 合并为「按秒 × 任意选择维度」；`per_image_openai_token` 拆成「按 token」与「按张」两条已有路径；`vidu_delegate` 可表达为「供应商积分」计量项加按秒兜底。
   - 合并后约为 5 种计价形态：按 token、按张、按秒、按次（含离散档）、按字符；供应商积分作为计量项附着在按张、按秒上。
4. **币种**：Google、OpenAI、xAI、Anthropic、Agnes 只有美元；火山方舟只有人民币；阿里云百炼、MiniMax、可灵、Vidu、DeepSeek、智谱、Kimi 按站点分别给出人民币和美元两套价目表，且两套不是汇率换算关系。币种应挂在价目表上，而不是挂在模型上。
5. **现有声明已与官方结构脱节的地方**（详见第 5 节）：Seedance 2.x 的定价键、可灵视频的档位轴、Agnes 的 `cached_input` 不被读取、部分内置模型已下架或改名。

## 1. 文本与 Agent 模型

### 1.1 维度事实表

| 供应商 | 缓存读 | 缓存写 | 缓存存储 | 请求级阶梯 | 音频输入单价 | 推理 token | Batch / 其他服务档 | 闲时价 | 币种 |
|---|---|---|---|---|---|---|---|---|---|
| Anthropic | 0.1×（部分模型 0.05× 或 0.025×） | 5 分钟 1.25×，1 小时 2× | 无 | 仅 Haiku 5.5：>100K 整请求切高价 | 不支持音频 | 计入输出 | Batch 0.5×；`inference_geo` 1.1×；Fast mode 另价 | 无 | 美元 |
| Gemini（AI Studio） | 有 | 未写明是否另计 | 每百万 token 每小时 | 3.1 Pro：>200K | Flash 系列音频为文本 2× | 计入输出 | Batch / Flex 0.5×，Priority 1.8× | 无 | 美元 |
| Gemini（Vertex） | 同上 | 同上 | 按 token·小时 | >200K 时输入和输出全部按长上下文价 | 同上 | 计入输出 | 同上；非 global 端点约 1.1× | 图片模型有 Off-peak 档 | 美元 |
| OpenAI | 0.1×（gpt-6.1-sol 0.05×） | GPT-5.6 起 1.25×，TTL 固定 30 分钟 | 无 | >272K 整请求切高价（输入、缓存 2×，输出 1.5×） | Realtime 模型音频单独计价 | 计入输出 | Batch / Flex 0.5×，Fast 2×，Ultrafast 6×；区域处理 +10% | 无 | 美元 |
| xAI | 有 | 未见 | 无 | ≥200K 整请求切高价，缓存 token 计入阈值 | 未见 | 按输出价 | Batch 按模型 0 或 20%；Priority 2×；美国区域端点 1.1× | 无 | 美元 |
| 火山方舟 | 约输入价 20% | 无 | 0.017 元/百万 token/小时 | 按输入长度分段，整请求同档 | 部分模型单列 | 不单列 | 低延迟约 3×，低优约 0.5×，批量约 0.5× | 仅托管的 deepseek 模型 | 人民币 |
| 阿里云百炼 | 隐式 20%，显式 10% | 显式缓存 125%，TTL 5 分钟 | 无 | 按单次请求输入总量分档，含输出在内全部按该档 | Omni 模型音频单列 | qwen-plus 思考输出另价 | Batch 0.5×，且不与缓存折扣叠加 | 无 | 人民币或美元（按站点） |
| DeepSeek | 有（命中价） | 无 | 无 | 无 | 不适用 | 未写明 | 未提及 | 有：高峰时段之外半价 | 人民币或美元（按站点） |
| 智谱 GLM | 有 | 无 | 元/百万 token/小时，目前多数限时免费 | 国内站部分模型按 32K 分档，输入、输出、缓存同时切换 | 视觉模型单独计价 | 未写明 | Batch 0.5× | 无 | 人民币或美元（按站点） |
| Kimi | 有 | 仅 kimi-k3：5 分钟和 1 小时两档 | 无 | 无 | 未写明 | 未写明 | 未提及 | 无 | 人民币或美元（按站点） |
| MiniMax | 有 | M2.7 有，M3 无 | 无 | M3：>512K 加倍 | 未写明 | 未写明 | Priority 1.5× | 无 | 人民币或美元（按站点） |
| Agnes | 有 | 无 | 无 | 无 | 未写明 | 未写明 | 无 | 无 | 美元 |

示例标价（每百万 token）：

- Anthropic Sonnet 4.6：输入 $3，5 分钟写 $3.75，1 小时写 $6，缓存读 $0.30，输出 $15。
- Gemini 3.1 Pro：≤200K 为 $2 / $12，缓存读 $0.20；>200K 为 $4 / $18，缓存读 $0.40；缓存存储 $4.50/小时。
- 豆包 Seed 2.0 Pro：[0,32K] 档输入 3.2 元、缓存命中 0.64 元、输出 16 元；(128K,256K] 档输入 9.6 元、输出 48 元。
- qwen3-max（北京）：0–32K 档 2.5 / 10 元，32K–128K 档 4 / 16 元，128K–256K 档 7 / 28 元。
- DeepSeek v4 Pro：缓存未命中输入 9 元（闲时 4.5 元），输出 27 元（闲时 13.5 元）。

### 1.2 归纳

- **推理 token 统一按输出计价。** 凡是写明的厂商（Anthropic、Gemini、OpenAI、xAI）都把推理 token 算进输出；只有 qwen-plus 对思考模式的输出另设单价。因此不需要单独的「推理 token」计量项，只需要一个「思考模式」选择维度，用来覆盖少数按模式定价的模型。
- **缓存是三个独立计量项：读、写、存储。**
  - 读：所有主流厂商都有，通常是输入价的 5%–20%。
  - 写：Anthropic、Kimi k3 按 TTL 分 5 分钟和 1 小时两档；OpenAI（GPT-5.6 起）、百炼显式缓存、MiniMax M2.7 只有一档。OpenAI 写明每个输入 token 只按未缓存、缓存命中、缓存写三者之一计一次，不是叠加费用。
  - 存储：Gemini 显式缓存、火山方舟、智谱按「token × 小时」计费。存储费取决于缓存存活时长，单次调用的用量里拿不到这个量，参考费用难以准确归属。
- **请求级阶梯的规则高度一致**：按单次请求的输入 token 总量（xAI 明确含缓存 token）选档，选中后输入、输出、缓存单价一起切换。区别只在阈值个数（1 个到 3 个）和阈值位置（32K、100K、128K、200K、256K、272K、512K）。智谱国内站文案提到「输入或输出长度超过阈值」，输出长度是否独立参与选档未能确认。
- **按输入模态区分单价，实际只发生在音频上。** 图片和视频输入在 Anthropic、方舟、Gemini 都按普通输入 token 计；音频输入在 Gemini Flash、方舟部分模型、百炼 Omni 和 OpenAI Realtime 单独计价。
- **服务档是乘数，不是新结构。** Batch / Flex 普遍为 0.5×，Priority 为 1.5×–2×，区域端点为 1.1×。百炼明确 Batch 不与缓存折扣叠加，方舟批量推理的缓存命中价另有比例，所以乘数并非处处可以直接相乘。
- **闲时价有两种形态。** DeepSeek（以及方舟托管的 deepseek 模型）按北京时间工作日时段区分高峰和闲时；Vidu 的错峰是请求参数 `off_peak`，任务最长 48 小时内完成。前者由调用时间决定，后者由请求选项决定。

## 2. 图片模型

| 计价结构 | 代表模型 | 维度 |
|---|---|---|
| 按张固定价 | Seedream 4.0 / 4.5 / 5.0-lite、qwen-image-2.0、wan2.7-image、MiniMax image-01、grok-imagine-image | 张数 |
| 按张 × 分辨率 | 可灵图像（1K/2K 同价，4K 翻倍）、Agnes（1K/2K/3K/4K）、Seedream 5.0-pro（以 2.61MP 为界）、qwen-image-3.0、Vidu viduq2 | 张数、分辨率 |
| 按张 × 分辨率 × 质量档 | grok-imagine-image-2.0、gpt-image-2 的按张折算价 | 张数、分辨率、质量档 |
| 按 token | Gemini 3 Pro Image、Gemini 3.1 Flash Image、gpt-image-2 | 文本输入、图片输入、缓存输入、文本输出、图片输出 token |
| 输入参考图附加费 | Seedream 5.0-pro（首张免费）、qwen-image-3.0、grok-imagine-image、Agnes（前 3 张免费）、Vidu viduq2（参考图档）、可灵 Image 2.1 多图参考 | 输入图张数、免费张数 |

要点：

- **Gemini 的「按分辨率每张价」只是 token 价的折算。** 官方按输出图片 token 计费，例如 Gemini 3 Pro Image 的 1K、2K 图为 1,120 token，4K 为 2,000 token，再乘每百万图片 token 单价。现有 `per_image_by_resolution` 用折算价表达，结果等价，但输入图片 token 和文本 token 被忽略。
- **OpenAI 的缓存输入只在 Responses API 的图片工具里生效**，`/v1/images` 不享受。
- **失败不计费**：百炼、方舟写明审核失败或请求失败不扣费。这与 ArcReel 现有「失败调用不记费」的行为一致，不需要额外维度。

## 3. 视频模型

| 计价结构 | 代表模型 | 维度 |
|---|---|---|
| 按秒 × 分辨率 | Grok Imagine Video、wan2.7、wan3.0、HappyHorse、MiniMax H3、Agnes video 2.5、Vidu Q3 / Q4 | 输出秒数、分辨率 |
| 按秒 × 分辨率 × 是否有声 | Veo 3.1（Vertex 分有声和纯视频两档，AI Studio 只列有声价）、wan2.6-r2v-flash | 输出秒数、分辨率、音频 |
| 按秒 × 分辨率 × 功能变体 | 可灵 3.0（有声、视频输入、运动控制各自另价，4K 不分有声） | 输出秒数、分辨率、音频、输入形态 |
| 按 (分辨率, 时长) 离散档 | MiniMax Hailuo 2.3 / 2.3-Fast / 02、Vidu Q1 / 2.0 | 每条视频、分辨率、时长 |
| 首秒 + 每增 1 秒 | Vidu Q2（例如 720P 首秒 15 积分，此后每秒 +10 积分；有声另加每任务 15 积分） | 按次基价、输出秒数、按次附加费 |
| 按 token（含公式） | Seedance 2.0 / 2.5 | 视频 token（=（输入视频时长 + 输出时长）× 宽 × 高 × 帧率 / 1024），单价按分辨率段和「是否含输入视频」选取 |
| 积分（响应回报实耗） | Vidu（任务详情返回 `credits`） | 供应商积分 × 积分单价 |

输入侧附加费（多家都有，现有 kind 一律没有表达）：

- **输入视频按秒计费**：MiniMax H3 按输出分辨率的秒价计；Agnes video 2.5 计费时长 = 输出时长 + 输入视频时长；wan2.7-r2v 计入输入视频时长（最多 5 秒）；wan3.0-video 计入输入时长；Grok Imagine Video 每秒输入视频 $0.01；Seedance 把输入视频时长计入 token。
- **输入图片超出免费张数后按张计费**：MiniMax H3（前 5 张免费）、Agnes（前 5 张免费）、Grok Imagine Video（每张 $0.01 或 $0.002）。
- **反例**：HappyHorse r2v 写明「仅输出计费」；可灵多图参考没有单独附加费。

其他：

- 服务档：Seedance 旧型号的离线推理（`service_tier=flex`）为在线的 50%，但 Seedance 2.0 / 2.5 不支持 flex；Vidu 错峰为半价且向上取整。
- 草稿模式：Seedance 2.5 草稿分两步计费，第一步按 480p。这是两次计费事件，可以按两次调用记账，不需要新维度。

## 4. 音频模型

| 计价结构 | 代表模型 | 说明 |
|---|---|---|
| 按字符 | qwen3-tts-flash（0.8 元/万字符）、MiniMax speech-2.8（国内站每万字符 2–3.5 元）、OpenAI tts-1 / tts-1-hd（每百万字符 $15 / $30）、Vidu TTS（每 500 字符 10 积分） | 百炼和 MiniMax 都规定一个汉字计 2 个字符 |
| 按 token | gpt-4o-mini-tts（文本输入 $0.60，音频输出 $12，每百万 token）、qwen-tts-flash、qwen-audio-3.1-tts-flash | 输入文本 token 与输出音频 token 分开计价 |
| 按次 | 可灵 TTS（每次 0.05 单位）、MiniMax 音色复刻（每个音色一次性收费） | 与时长、字数无关 |

「汉字计 2 个字符」是计数规则，影响的是用量而不是单价。`per_character` 把字符数放在 `usage_tokens` 中传入，由调用方计数；`lib/backends/audio_backends/dashscope.py` 当前用 `len(request.text)` 计数，纯中文文本的参考费用因此约为实际的一半。计量项需要能声明计数规则（例如「CJK 字符权重 2」）。

## 5. 币种分布

| 只有美元 | 只有人民币 | 两套价目表（按站点） |
|---|---|---|
| Google（AI Studio、Vertex）、OpenAI、xAI、Anthropic、Agnes | 火山方舟（国际站 BytePlus 是另一个平台，本次未查） | 阿里云百炼、MiniMax、可灵、Vidu、DeepSeek、智谱、Kimi |

要点：

- **两套价目表不是汇率换算。** 例如 MiniMax H3-Max 的输入视频价在两站之间不符合汇率；可灵国内站视频 1 积分 = ¥1，国际站 1 单位 = $0.14。
- **同一站点内也可能混用积分。** 可灵视频积分与图像积分是两种单位（国内站视频 1 积分 = ¥1，图像 1 积分 = ¥0.025）；Vidu 国内站 1 积分 = ¥0.03125，国际站标准价 $0.005/积分。
- 百炼中文页的国际地域标签也显示人民币价（看起来是换算值），英文页对国际部署显示美元。以英文页作为国际价的依据更稳妥。

## 6. 最少计价维度

### 6.1 统一公式

```text
费用 = 服务档乘数 × Σ_计量项 [ max(用量 − 单请求免费额度, 0) × 单价(计量项 | 选择维度, 请求级阶梯) ]
币种 = 价目表币种
```

按次计价（离散档、按次附加费）视为用量恒为 1 的计量项。

### 6.2 维度清单

| 维度 | 取值 | 必要性依据 |
|---|---|---|
| **计量项** | token：输入、输出、缓存读、缓存写（5 分钟 / 1 小时）、缓存存储（token·小时）；token 可按模态细分（文本、图片、音频）；张数：输出图、输入图；秒数：输出视频、输入视频；字符数；按次；供应商积分 | 所有厂商的账单行都能落到这些单位上 |
| **单价选择维度** | 分辨率、质量档、是否有声、输入形态（是否含参考视频、运动控制等）、时长档、思考模式 | 图片和视频的单价全部由这些维度组合选出 |
| **请求级阶梯** | 按单次请求输入 token 总量的一组阈值；选中后整请求同档 | Gemini、Vertex、OpenAI、xAI、方舟、百炼、智谱、MiniMax、Anthropic Haiku 5.5 规则一致 |
| **单请求免费额度** | 每个计量项可声明免费数量 | 参考图前 N 张免费、Vidu Q2 首秒另计 |
| **修饰项** | 服务档乘数（含「是否与缓存折扣叠加」）、闲时价（按时段或按请求选项）、价目表币种 | Batch、Flex、Priority、区域端点、DeepSeek 闲时、Vidu 错峰 |

### 6.3 建议不建模的部分

参考费用的定位允许以下各项不进入计价结构：

- 预置吞吐、TPM 保障包、模型单元、订阅套餐和资源包。
- 限时促销（例如 Vertex 的 credits back、Agnes 的限时免费）、新用户额度。
- 违规请求费（xAI 每次 $0.05）、版权 IP 附加费（Seedance）。
- 缓存存储费：用量随缓存存活时间变化，单次调用无法归属。可以只在价目表中保留单价字段，计算时不计入。

联网搜索等工具调用费（Anthropic 每千次 $10、xAI 每千次 $5、Gemini 超出免费额度后每千次查询 $14）可以用「按次」计量项表达。Agent 是否会大量产生这类费用，由 Agent 计费相关的票决定。

### 6.4 与 LiteLLM 字段的对照

LiteLLM 的 `model_prices_and_context_window.json` 用扁平字段名表达同一组维度，可作为参考：

- 计量项：`input_cost_per_token`、`output_cost_per_token`、`cache_read_input_token_cost`、`cache_creation_input_token_cost`、`cache_creation_input_token_cost_above_1hr`、`input_cost_per_audio_token`、`output_cost_per_image`、`output_cost_per_second`、`input_cost_per_character` 等。
- 请求级阶梯：`_above_{N}k_tokens` 后缀（N 有 32、100、128、200、256、272、512），以及百炼使用的 `tiered_pricing` 区间数组。
- 服务档：`_batches`、`_priority`、`_flex` 后缀。
- 闲时价：`off_peak_pricing`，带 UTC 时段窗口。
- 区域：`regional_endpoint_uplift_multiplier` 等。
- 没有币种字段，全部按美元计。这一点不适合 ArcReel：国内厂商只有或优先使用人民币价目表。

LiteLLM 的做法证明这组维度够用，但字段名后缀的组合方式（例如 `cache_creation_input_token_cost_above_1hr_above_200k_tokens`）会随维度相乘膨胀。ArcReel 用「计量项 + 选择维度 + 阶梯」的结构化形式，可以避免这种膨胀。

## 7. 与现有 10 种 PricingKind 的对照

| kind | 结论 | 说明 |
|---|---|---|
| `per_token` | **需扩展** | 只有输入、输出两个计量项。缺缓存读、缓存写（含 TTL 两档）、请求级阶梯、音频输入单价、思考模式。`_agnes_text_pricing` 已在 `rates` 中写入 `cached_input`，但 `_per_token` 只读 `input` 和 `output`，这个缓存命中价从未生效。 |
| `per_image_flat` | **可合并** | 是 `per_image_by_resolution` 只有一个档位的特例。 |
| `per_image_by_resolution` | **可覆盖，需小幅扩展** | 合并 `per_image_flat` 后作为「按张 × 选择维度」。需加质量档（grok-imagine-image-2.0）和输入参考图附加费（含免费张数）。 |
| `per_image_openai_token` | **可拆分合并** | token 主路径就是按模态细分的 `per_token`（文本输入、图片输入、缓存输入、图片输出）；兜底路径就是「按张 × (质量, 尺寸)」。Gemini 图片模型同样是按 token 计费，可共用这条路径。 |
| `per_second_matrix` | **可合并，需扩展** | `resolution_audio`、`resolution_only`、`flat` 三种 `dimensions` 只是选择维度的不同子集。需加输入视频秒数、输入图附加费、功能变体维度。 |
| `per_second_tiered` | **可合并** | 「质量档 × 是否有声」也是选择维度的一个子集。另外，可灵当前官方价目表以分辨率（720P / 1080P / 4K）为轴，已不再出现 std / pro，现有档位派生逻辑需要按新表核对。 |
| `per_video_bucket` | **可覆盖** | 结构正确，对应「按次 + (分辨率, 时长) 选择维度」。Vidu Q1 / 2.0 也属于这种结构。 |
| `per_token_video` | **需扩展** | 选择维度错位：现有键为 `(service_tier, generate_audio)`，而 Seedance 2.0 / 2.5 的官方单价按分辨率段（480p/720p、1080p、4K）和「是否含输入视频」选取，不区分有声。registry 中 2.x 模型只填了 480p/720p 无输入视频档的单价。 |
| `per_character` | **可覆盖** | 结构正确。字符计数口径需要支持「汉字计 2 个字符」（见第 4 节）。 |
| `vidu_delegate` | **可合并** | 主路径是「供应商积分 × 积分单价」，兜底是按秒 × 分辨率的积分表。积分作为计量项、错峰作为修饰项后，不再需要委托给 backend 代码。Vidu Q2 的「首秒 + 增量」需要单请求免费额度或按次基价。 |

合并后的形态：

| 形态 | 吸收的现有 kind |
|---|---|
| 按 token | `per_token`、`per_image_openai_token` 的 token 路径、`per_token_video` |
| 按张 | `per_image_flat`、`per_image_by_resolution`、`per_image_openai_token` 的兜底路径 |
| 按秒 | `per_second_matrix`、`per_second_tiered` |
| 按次 | `per_video_bucket` |
| 按字符 | `per_character` |

`vidu_delegate` 由「供应商积分」计量项吸收，可挂在按张或按秒上。

### 自定义模型的缺口

自定义模型只有 `price_input`、`price_output`、`currency` 三个字段，`_calculate_custom_cost` 按 `call_type` 硬算：文本按输入、输出 token；图片按张固定价；视频按秒固定价（缺省 8 秒）；音频按每万字符。与上面的维度清单相比，自定义模型至少缺缓存读、缓存写和请求级阶梯（文本），以及分辨率选择维度（图片和视频）。`price_unit` 字段被存储但不参与计算。

## 8. 现有内置声明与官方现状的偏差

这些是调研中顺带发现的事实偏差，不是计价结构问题，供后续票参考：

- **已下架或改名的模型**：
  - Sora 2 / Sora 2 Pro 及 Videos API 已于 2026-09-24 关闭（OpenAI 视频生成指南）。
  - `grok-imagine-image-pro` 不在 xAI 当前模型和价格列表中，当前图片模型为 `grok-imagine-image`、`grok-imagine-image-2.0`、`grok-imagine-image-quality`。
  - `doubao-seed-1-8` 与 `doubao-seedance-1-5-pro` 不在方舟当前价格页和视频 API 文档中。
  - MiniMax `S2V-01` 不在国内站和国际站的按量计费页中。
  - AI Studio 定价页对图片模型使用 `gemini-3.1-flash-image` 等名称，不再列出 `-preview` 后缀的 ID。
- **结构偏差**：Seedance 2.x 的定价键（见第 7 节）；可灵视频从 std / pro 档改为分辨率档，且有声加价幅度与现有矩阵不同（例如 3.0 有声 720P 为 0.9 单位/秒，现有矩阵 std 有声为 0.8）。
- **服务档**：OpenAI 的 Priority 已于 2026-07-30 改名 Fast。

## 9. 未查明项

- 智谱国内站的阶梯是否按输出长度独立选档：只读到常见问题文案，未读到价格表原文。
- Gemini 显式缓存的首次写入是否按普通输入计价：官方文档未写明。
- OpenAI `prompt_cache_retention=24h` 是否额外收费：官方文档未写明。
- DeepSeek、Kimi 的推理 token 计数口径：官方定价页未写明。
- Vidu 国际站的按模型积分表为动态加载，未能确认与国内站积分消耗一致；Vidu Q2 完整价目表在飞书文档中，未读取。
- 火山方舟国际站（BytePlus）未查。
- 百炼各模型的缓存命中具体单价不在主价格表中，只有比例（20%、125%、10%）；qwen3.8 系列写明「以控制台为准」。
- MiniMax H3 是否对音频输出另行计价：只写明音频输入免费。

## 来源

访问日期均为 2026-10-08。

- Anthropic：[Pricing](https://platform.claude.com/docs/en/about-claude/pricing)、[Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)、[Vision](https://platform.claude.com/docs/en/build-with-claude/vision)、[Thinking cost](https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost)
- Google：[Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)、[Context caching](https://ai.google.dev/gemini-api/docs/generate-content/caching)、[Vertex AI generative AI pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing)
- OpenAI：[Pricing](https://developers.openai.com/api/docs/pricing)、[Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)、[Image generation](https://developers.openai.com/api/docs/guides/image-generation)、[Video generation](https://developers.openai.com/api/docs/guides/video-generation)、[gpt-5.5](https://developers.openai.com/api/docs/models/gpt-5.5)、[gpt-6.1-sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
- xAI：[Pricing](https://docs.x.ai/developers/pricing)、[Prompt caching usage and pricing](https://docs.x.ai/developers/advanced-api-usage/prompt-caching/usage-and-pricing)、[grok-imagine-video-1.5](https://docs.x.ai/developers/models/grok-imagine-video-1.5)、[Video generation](https://docs.x.ai/developers/model-capabilities/video/generation)
- 火山方舟：[模型价格](https://www.volcengine.com/docs/82379/1544106)、[模型服务计费说明](https://docs.volcengine.com/docs/ark/model-service-pricing?lang=zh)、[视频生成 API](https://www.volcengine.com/docs/82379/1520757)
- 阿里云百炼：[模型价格（中文）](https://help.aliyun.com/zh/model-studio/model-pricing)、[上下文缓存](https://help.aliyun.com/zh/model-studio/context-cache)、[Model pricing（英文）](https://www.alibabacloud.com/help/en/model-studio/model-pricing)、[Context cache（英文）](https://www.alibabacloud.com/help/en/model-studio/context-cache)
- MiniMax：[按量计费（国内站）](https://platform.minimax.cn/docs/guides/pricing-paygo.md)、[Pay as you go（国际站）](https://platform.minimax.io/docs/guides/pricing-paygo.md)
- 可灵：[视频计费（国内站）](https://klingai.com/document-api/pricing/base/video)、[图像计费（国内站）](https://klingai.com/document-api/pricing/base/image)、[Video pricing（国际站）](https://kling.ai/document-api/pricing/base/video)、[Image pricing（国际站）](https://kling.ai/document-api/pricing/base/image)
- Vidu：[价格说明（国内站）](https://platform.vidu.cn/docs/overview/pricing)、[任务详情](https://platform.vidu.cn/docs/api-reference/task-management/task-detail)、[Vidu Q3 文生视频](https://platform.vidu.cn/docs/api-reference/video-models/vidu-q3/text-to-video)、[Pricing（国际站）](https://platform.vidu.com/pricing)
- Agnes：[Pricing](https://agnes-ai.com/en/docs/pricing)、[Token plan](https://agnes-ai.com/en/docs/tokenplan)、[Model catalog](https://github.com/AgnesAI-Labs/AgnesAI-Models/blob/main/MODEL_CATALOG.md)
- DeepSeek：[Pricing](https://api-docs.deepseek.com/quick_start/pricing)、[模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)
- 智谱：[价格（国内站）](https://open.bigmodel.cn/pricing)、[Pricing（z.ai）](https://docs.z.ai/guides/overview/pricing)
- Kimi：[价格（国内站）](https://platform.kimi.com/docs/pricing/chat)、[Pricing（国际站）](https://platform.kimi.ai/docs/pricing/chat)
- LiteLLM：[model_prices_and_context_window.json](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json)
- 仓库代码：`lib/billing/pricing/types.py`、`lib/billing/pricing/strategies.py`、`lib/billing/cost_calculator.py::CostCalculator._calculate_custom_cost`、`lib/config/registry.py`（计价构造函数与 `PROVIDER_REGISTRY`）、`lib/backends/vidu_shared.py::calculate_vidu_cost`、`docs/adr/0009-declarative-model-pricing.md`
