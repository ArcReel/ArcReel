# 调研：多模态模型的视频审阅能力

**调研日期**：2026-09-24
**关联**：#2670（隶属 Wayfinder #2667「Agent 自动剪辑」）
**问题**：多模态模型审阅"生成视频"的能力到了什么程度？能不能支撑 L2 看素材剪？
**代码基线**：`main` @ 9aa1e2c6

**来源与口径**：
- 模型能力与价格全部取自厂商官方文档和定价页，论文数据取自 arXiv 原文。每条结论后都附了链接。
- 无法一手确认的内容标注「未一手确认」。
- 价格取标准在线推理的最低输入长度档，不含 Batch/Flex 折扣和限时促销。
- "单条 10 s 成本"只计视频/图片输入 token，不含提示词和输出，是按厂商公开的 token 公式推算的，不是账单实测值。
- 本文所说的"片段"指 ArcReel 单次生成的视频，通常 4–15 s、720p/1080p，部分模型会带音轨。

---

## 0. 结论先行

1. **原生视频输入已经普及，而且便宜。** 以下模型都能直接吃视频文件：
   - 国外：Gemini（全系）、Amazon Nova 2 Lite。
   - 国内：Qwen3.x / Qwen3-VL / Qwen-Omni、豆包 Seed 2.x、GLM-4.6V / 5.x、Kimi K2.6 / K3、MiniMax-M3。

   审一条 10 s 片段大约只要 ¥0.001–0.09（Gemini 约 $0.0003–0.006）。Claude、OpenAI GPT、Grok、DeepSeek **不接受视频**，只能抽帧后按多图输入。
2. **能听懂音轨的只有少数几个：** Gemini、Qwen-Omni 系列、豆包 Seed 2.0 lite/mini 的 260428 快照。Qwen3.x/VL 和 Seed 2.1 Pro 都明确**不理解视频里的音频**。
3. **能力上，缺陷检测靠不住，时间戳只是粗定位。**
   - 2025–2026 年的基准一致表明：通用 MLLM 零样本检测生成视频缺陷的召回很低，给出的时间段也基本不准。比如 Spotlight 基准里，Gemini 2.5 Pro 只覆盖了约 12.6% 的错误，人类是 26.8%；Artifact-Bench 的细粒度缺陷识别，所有模型准确率都不到 10%。
   - 模型相对最擅长的是"动作与提示词不符"，相关系数约 0.7。肢体崩坏、闪烁、物体凭空出现或消失这几类最弱。
   - 通用时间定位的 mIoU 大约在 0.4–0.65 之间，意思是秒级误差很常见。而缺陷本身往往只持续 1–3 s。
4. **ArcReel 目前没有任何一条通道能把视频送给模型。**
   - `TextGenerationRequest` 只有 `images` 字段，四个文本后端只会发图片。
   - 能力词表里只有 `vision`（图片），没有视频输入这一项。
   - 也就是说，现在唯一可行的做法是"抽帧 + 多图"：OpenAI、Grok、Gemini、豆包、MiniMax-M3 已经声明了 `vision`，可以直接用。
   - 阿里百炼已接入的 Qwen3.6-Plus 在官方文档里是原生多模态，但 registry 里没有声明 `vision`。
5. **推荐方案是混合审阅。**
   - 确定性信号优先：ffmpeg 负责检测首尾冗余和冻结帧，OCR 负责检测文字和水印，这两类能给出精确的入出点。
   - 原生视频 MLLM 负责逐片段评估"是否符合提示词"和"有没有明显崩坏"，给出粗定位和可用区间的建议。
   - 没有视频输入的模型退回到抽帧方案。
   - MLLM 的结论只用来排序、标记和给出建议剪点，**不作为自动废弃素材的唯一依据**。

---

## 1. 原生视频输入：各家能力与限制

### 1.1 Google Gemini（Gemini API / Vertex AI）

| 项 | 说明 |
|---|---|
| 输入方式 | 请求内联（官方页面同时写着 20 MB 和 100 MB 两种上限，以较保守的 20 MB 为准）；Files API（付费档单文件 20 GB）；GCS（2 GB）；公开 YouTube 视频 |
| 时长 | 1M 上下文下，低分辨率约 3 h，高分辨率约 1 h |
| 采样 | 默认 1 fps，可用 `videoMetadata.fps` 自定义；可用 `startOffset`/`endOffset` 裁剪（仅 static 模式支持） |
| 分辨率与 token | Gemini 3：`low`/`medium`/默认每帧 70 token，`high` 每帧 280 token；音频 25 token/s（见 media-resolution 页）。视频理解页仍写着旧口径：每帧 66/258 token、音频 32 token/s。**两处不一致，预算前应先用 countTokens 或 usage_metadata 实测** |
| 音轨 | **理解。** 官方原文是同时处理 "both the audio and visual streams" |
| 时间戳 | 提示词里可以用 `MM:SS` 指代时刻，也可以要求输出时间戳。Vertex 建议 fps 大于 1 时使用 `MM:SS.sss` |
| 当前模型 | 3.8/3.7/3.6/3.5 Flash、3.5 Flash-Lite、3.1 Flash-Lite、3.1 Pro Preview。2.5 系列只对老用户开放 |

价格（输入，每 1M token）如下。10 s 片段按 1 fps、含音频计，默认档约 950 token，`high` 档约 3,050 token。

| 模型 | 输入价 | 10 s 默认档 | 10 s high |
|---|---|---|---|
| gemini-3.8-flash | $0.75（2026-12-31 前），之后 $1.50 | ≈$0.0007 | ≈$0.0023 |
| gemini-3.5-flash-lite | $0.30 | ≈$0.0003 | ≈$0.0009 |
| gemini-3.1-flash-lite | $0.25（音频 $0.50） | ≈$0.0003 | ≈$0.0008 |
| gemini-3-flash-preview | $0.50（音频 $1.00） | ≈$0.0006 | ≈$0.0017 |
| gemini-3.1-pro-preview | $2.00（≤200k） | ≈$0.0019 | ≈$0.0061 |

以上价格的前提是视频内嵌音轨按音频单价计费，这一点官方没有明说（未一手确认）。Batch 和 Flex 为 5 折。

来源：
- [video-understanding](https://ai.google.dev/gemini-api/docs/video-understanding)
- [media-resolution](https://ai.google.dev/gemini-api/docs/media-resolution)
- [file-input-methods](https://ai.google.dev/gemini-api/docs/file-input-methods)
- [pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [models](https://ai.google.dev/gemini-api/docs/models)
- [Vertex video-understanding](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/video-understanding)

### 1.2 阿里百炼：Qwen3.x / Qwen3-VL / Qwen-Omni

| 模型 | 视频上限 | fps 默认/范围 | 音轨 | token | 输入价（每 1M） | 10 s 估算 |
|---|---|---|---|---|---|---|
| qwen3.8-max / 3.8-flash / 3.7-plus / 3.6-plus / 3.5-plus（原生多模态） | 2 s–2 h；URL ≤2 GB，Base64 ≤10 MB，本地文件 ≤100 MB | 2 / [0.1, 10] | **不理解** | 32×32 px 折 1 token，每 2 帧合并计。720p/1080p 会被缩到单帧像素上限，约 720 token/s | 3.8-max ¥12、3.7-plus ¥2、3.6-plus ¥2、3.8-flash ¥0.8、3.5-plus ¥0.8、3.7-flash ¥0.2 | 3.7-plus ≈¥0.014；3.8-flash ≈¥0.006 |
| qwen3-vl-plus / qwen3-vl-flash | 2 s–1 h | 2 / [0.1, 10] | 不理解 | 约 594 token/s | plus ¥1、flash ¥0.15 | plus ≈¥0.006；flash ≈¥0.0009 |
| **qwen3.8-omni-flash** | 2 h；URL ≤2 GB | 按 2 fps 估算（用户 fps 参数未一手确认） | **理解**（音频 7 token/s） | 约 594 token/s | ¥0.8 | ≈¥0.005 |
| qwen3.5-omni-plus / flash | 1 h | 同上 | 理解 | 同上 | 文本/图片/视频 ¥7 / ¥2.2；音频 ¥53 / ¥18 | plus ≈¥0.045；flash ≈¥0.014 |

补充：
- 提供 OpenAI 兼容接口（`compatible-mode/v1`），通过 `video_url` 和 `fps` 字段传视频。视频也可以用图片列表的形式传入。
- Batch 5 折。
- Omni 旧型号（qwen3-omni-flash）要求 `stream=True`。

时间戳方面：
- Qwen3-VL 模型卡自称具备 "Text–Timestamp Alignment … second-level indexing"。
- 百炼文档给出了"定位事件并输出 `start_time`/`end_time`"的示例。
- Omni 文档推荐输出 `[hh:mm:ss:xxx-hh:mm:ss:xxx]` 格式的时间段。

来源：
- [vision](https://help.aliyun.com/zh/model-studio/vision)
- [qwen-omni](https://help.aliyun.com/zh/model-studio/qwen-omni)
- [model-pricing](https://help.aliyun.com/zh/model-studio/model-pricing)
- [Qwen3-VL 模型卡](https://huggingface.co/Qwen/Qwen3-VL-235B-A22B-Instruct)

### 1.3 火山方舟：豆包 Seed 2.x

| 模型 | 视频上限 | fps 默认/范围 | 音轨 | token | 输入价（每 1M） | 10 s 估算 |
|---|---|---|---|---|---|---|
| doubao-seed-2.1-pro（260915） | URL/Base64 ≤50 MB；Files API ≤512 MB | 1 / [0.2, 5]，帧数限制在 [16, 1280] | **不理解** | 每帧 [64, 384] token | ¥6（2.1-turbo ¥3；2.0-pro ¥3.2） | ≈¥0.037 |
| doubao-seed-2.0-lite / 2.0-mini（**260428 快照**） | 同上 | 同上 | **理解**（自动抽取内嵌音轨，约 6.25 token/s） | 同上 | 2.0-lite ¥0.6、2.0-mini ¥0.2 | 2.0-lite ≈¥0.004；2.0-mini ≈¥0.0014 |

补充：
- Chat API 用 `video_url` 传视频，官方示例使用 OpenAI SDK。
- 机制上，方舟会在每帧前插入时间戳文本（Seed 2.0 起格式为 `4.0 second`）。文档称模型可以"回答事件发生什么时间点"。
- 最少 16 帧，所以 16 s 以内的片段成本一样。
- 每帧默认 384 token 这一点，文档表述有歧义（未一手确认）。
- Seed 1.6 / 1.8 已不在当前模型列表和定价页上，是否下线未一手确认。

来源：
- [视频理解](https://www.volcengine.com/docs/82379/1895586)
- [音频理解](https://www.volcengine.com/docs/82379/2377589)
- [模型列表](https://www.volcengine.com/docs/82379/1330310)
- [定价](https://www.volcengine.com/docs/82379/1544106)

### 1.4 其他国内厂商（简述）

| 模型 | 原生视频 | 已知限制 | 音轨 | 输入价（每 1M） | 来源 |
|---|---|---|---|---|---|
| 智谱 GLM-5.3-Flash / FlashX、GLM-5V-Turbo、GLM-4.6V 系列 | 是（`video_url`） | ≤200 MB；时长和 fps 未一手确认 | 未一手确认 | 5.3-Flash ¥0.8、4.6V ¥1、4.6V-Flash 免费 | [对话补全](https://docs.bigmodel.cn/api-reference/模型-api/对话补全.md)、[定价](https://docs.bigmodel.cn/cn/guide/start/pricing.md) |
| Moonshot kimi-k3 / kimi-k2.6 | 是（先上传，再用 `ms://` 引用） | 请求体 ≤100 MB，建议 ≤1080p | 未一手确认 | k3 ¥20、k2.6 ¥6.5 | [视觉指南](https://platform.kimi.com/docs/guide/use-kimi-vision-model) |
| MiniMax-M3 | 是（`video_url`） | URL/Base64 ≤50 MB；fps 1 / [0.2, 5] | 未一手确认 | ¥2.1（五折价） | [OpenAI API](https://platform.minimax.cn/docs/api-reference/text-chat-openai.md)、[定价](https://platform.minimax.cn/docs/guides/pricing-paygo.md) |
| DeepSeek | **否**。deepseek-flash 仅支持图片，v4-pro 仅支持文本 | — | — | — | [vision](https://api-docs.deepseek.com/zh-cn/guides/vision) |

GLM-4.6V 文档自称能"精准定位答案所在时间段"。Kimi、MiniMax、DeepSeek 的文档里都没有时间戳相关的声明。

### 1.5 其他国外厂商

- **Amazon Nova 2 Lite**
  - 支持原生视频：每次请求 1 个视频，内联 ≤25 MB，经 S3 ≤1 GB。
  - 采样 1 fps，每帧被拉伸到 672×672。
  - **不理解音轨**。价格页为动态渲染，没能取到（未一手确认）。
  - 来源：[Nova 2 用户指南](https://docs.aws.amazon.com/nova/latest/nova2-userguide/using-multimodal-models.html)
- **xAI Grok、Mistral**：理解侧只接受图片，不接受视频。来源：[xAI models](https://docs.x.ai/developers/models)、[Mistral vision](https://docs.mistral.ai/studio-api/conversations/vision)

---

## 2. 不支持视频的模型：抽帧 + 多图

### 2.1 厂商口径

- **Anthropic Claude**
  - 所有现役模型都只支持 "text and image input"，没有视频输入。
  - 每次请求最多 600 张图（200k 上下文的模型，如 Haiku 4.5，为 100 张）；超过 20 张时，建议每边 ≤2000 px。
  - token 公式已改为按 28×28 px 切块：⌈w/28⌉×⌈h/28⌉。1280×720 约 1,196 token，768×432 约 448 token。
  - 来源：[vision](https://platform.claude.com/docs/en/build-with-claude/vision)、[pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- **OpenAI**
  - Responses API 只接受 "text and image inputs"；Videos API 只做生成，不做理解。
  - 官方 Cookbook 的做法是：用 OpenCV 抽帧（每 25 帧取 1 帧），逐张作为 `input_image` 发送。
  - 新模型（GPT-5.x、6）按 32 px 切块计费，再乘系数 1.2。1280×720 在 `high` 档约 1,104 token，在 `low` 档（缩到 512 内）约 173 token。
  - 每次请求最多 1,500 张图。
  - 来源：[images-vision](https://developers.openai.com/api/docs/guides/images-vision)、[Cookbook](https://developers.openai.com/cookbook/examples/gpt_with_vision_for_video_understanding)、[pricing](https://developers.openai.com/api/docs/pricing)

### 2.2 单条 10 s 片段抽 10 帧的成本

| 模型 | 输入价（每 1M） | 10 帧 @1280×720 | 10 帧 @低分辨率 |
|---|---|---|---|
| claude-opus-5-5 | $4 | ≈$0.048 | ≈$0.018（768 长边） |
| claude-sonnet-5 | $2 | ≈$0.024 | ≈$0.009 |
| claude-haiku-4-5 | $1 | ≈$0.012 | ≈$0.0045 |
| gpt-5.5 | $5 | ≈$0.055 | ≈$0.0087（low） |
| gpt-5.4 | $2.5 | ≈$0.028 | ≈$0.028（该尺寸下 `low` 不比 `high` 便宜） |
| gpt-5.6-luna | $0.2 | ≈$0.0022 | ≈$0.00035 |

同样 10 s 的内容，抽帧方案约需 4k–12k token；原生视频走 Gemini 3 只要约 950 token，还自带音频。

### 2.3 推荐抽帧策略

这部分是综合厂商口径与第 3 节基准证据后给出的工程建议，不是某家厂商的规范。

1. **分三路采帧。**
   - **均匀采样**：2 fps，10 s 取 20 帧。Spotlight 基准中 Gemini 和 Qwen 都用 2 fps 评测。Moment-Video 显示 Gemini-3.1-Pro 从 1 fps 升到 5 fps 时，瞬态事件准确率从 26.9% 升到 38.3%，再往上就饱和了。
   - **镜头切换帧**：用 ffmpeg 的 `select='gt(scene,x)'` 取。
   - **首尾加密**：首尾各 0.5 s 内加密采样，用来判断冗余帧。
2. **分辨率。** 长边 512–768 px 就够判断构图和崩坏。检查文字或水印时，单独对可疑帧用高分辨率复查。
3. **时间戳必须显式给出。** 每帧前插入 `t=3.5s` 这样的文本（方舟也是这么做的），或者把时间码烧进画面。否则模型无法回答"在哪一秒"。
4. **控制图片数量。** 可以把 4–9 帧拼成一张带时间码的宫格图，降低图片数和请求体积，代价是单帧分辨率下降。
5. **音轨另外处理。** 抽帧方案下模型听不到声音，需要另走 ASR 或音频模型。

---

## 3. 能否可靠识别生成视频缺陷并给出时间戳

### 3.1 按缺陷类型汇总

| 缺陷 | 通用 MLLM 零样本 | 时间戳 | 更可靠的做法 | 主要证据 |
|---|---|---|---|---|
| 肢体、手、脸崩坏 | **低**：Spotlight Anatomy 类 Gemini 2.5 Pro 0.078，人类 0.153 | 低，错误持续时间短 | 帧级专用异常检测器（VBench-2.0 Human Anatomy） | [Spotlight](https://arxiv.org/abs/2511.18102)、[VBench-2.0](https://arxiv.org/abs/2503.21755) |
| 角色前后不一致 | **中偏低**：GPT-4o 在时间一致性上与人类的 ρ 为 0.40 | 低 | RetinaFace + ArcFace 逐帧相似度，或 DINO 特征；MLLM 只负责路由和解释 | [Video-Bench](https://arxiv.org/abs/2504.04907)、[MSVBench](https://arxiv.org/abs/2602.23969) |
| 闪烁、形变、物体凭空出现或消失、物理违规 | **低**：Artifact-Bench 细粒度识别所有模型不到 10%（人类 80.3%）；物理常识与人类 r≈0.11 | 低，模型常把整段视频当作出错区间 | VBench 的 Temporal Flickering、Subject Consistency、Motion Smoothness 等逐帧指标 | [Artifact-Bench](https://arxiv.org/abs/2605.18984)、[VideoPhy-2](https://arxiv.org/abs/2503.06800)、[VBench](https://arxiv.org/abs/2311.17982) |
| 画面文字、水印 | 读字中等（MME-VideoOCR 最好 73.7%）；检测乱码或水印**没有基准** | 逐帧 OCR 可给出区间 | 逐帧 OCR + 规则 | [MME-VideoOCR](https://arxiv.org/abs/2505.21333) |
| 首尾冗余帧、冻结帧 | 没有 MLLM 基准 | 经典方法**高**，可精确到帧 | ffmpeg `freezedetect` 直接输出起止时间，或相邻帧 SSIM/MAE | [ffmpeg freezedetect](https://ffmpeg.org/ffmpeg-filters.html#freezedetect) |
| 动作与提示词不符 | **中**，是模型最强的一类：Video-Bench 动作一致性 ρ 0.72；Spotlight Adherence 类覆盖 20.8% | 中偏低 | MLLM + VQAScore/ViCLIP；关键维度用检测或跟踪指标 | [Video-Bench](https://arxiv.org/abs/2504.04907)、[T2V-CompBench](https://arxiv.org/abs/2407.14505) |

### 3.2 关键数据

**Spotlight（2025-11）**
- 目前唯一同时要求"指出错误 + 给出时间段"的基准。测试视频来自 Veo 3、Seedance、LTX-2，共 600 条，含 1604 处错误标注。
- 在 S+P@0.7 指标下：Gemini 2.5 Pro 0.250，覆盖 12.6%；Qwen3-VL-8B 0.148；人类 0.508，覆盖 26.8%。
- 原文指出模型 "often predicts the entire duration of the clip as the time segment"。
- 错误片段平均 2.49 s，视频平均约 6.5 s。

**Artifact-Bench（2026-05）**，评测了 19 个 MLLM：
- 真假二分类：Gemini 3.1 Pro 74.0%，人类 93.6%。
- 两两比较真实感：Gemini 3.1 Pro 48.6%，接近随机。
- 论文结论："do not reliably base their judgments on genuine artifact-aware perception"。

**VideoScore2**
- 1–5 分精确一致率：Claude-Sonnet-4 28.9%、Gemini-2.5-Pro 27.9%、GPT-5 26.2%；专门微调的 VideoScore2 为 44.4%。
- 来源：[arXiv 2509.22799](https://arxiv.org/abs/2509.22799)

**通用时间定位**

| 基准 | 模型与成绩 |
|---|---|
| Charades-STA mIoU（厂商自报） | Qwen3-VL-235B 64.8、Seed1.5-VL 64.7、Qwen2.5-VL-72B 50.9、GPT-4o 35.7 |
| 修正后的 Charades-TimeLens，R1@0.5 | Gemini-2.5-Pro 61.1、Qwen3-VL-8B 53.4、GPT-5 42.0 |

- 第三方复现 Qwen2.5-VL-7B 只得到 29.5 mIoU，自报为 43.6。
- Gemini 2.5 技术报告的案例里，时间码 3 次只对 1 次，另 2 次偏差在 3 s 以内。
- 来源：[Qwen3-VL](https://arxiv.org/abs/2511.21631)、[Seed1.5-VL](https://arxiv.org/abs/2505.07062)、[TimeLens](https://arxiv.org/abs/2512.14698)、[Gemini 2.5](https://arxiv.org/abs/2507.06261)、[lmms-eval #857](https://github.com/EvolvingLMMs-Lab/lmms-eval/issues/857)

**瞬态事件（Moment-Video，2026-06）**
- 最好的 Seed-2.0-Pro 为 39.6%，人类 84.3%。
- 低 fps 会漏掉瞬态证据，但只靠加帧也补不齐。
- 来源：[arXiv 2606.02522](https://arxiv.org/abs/2606.02522)

**结论**：对 L2 来说，MLLM 给出的时间戳只能当"大约在这附近"的提示，精确入出点要靠逐帧的确定性信号来校准。已有的基准都没有覆盖 Claude 对生成视频缺陷的定位能力，GPT-5.x 也只出现在少数几个基准里。

---

## 4. ArcReel 现状：已接入的文本供应商能否用于视频审阅

### 4.1 代码事实

- **请求模型只支持图片。** `lib/backends/text_backends/base.py` 中的 `TextGenerationRequest` 只有 `images: list[ImageInput]`，没有视频字段。`TextCapability` 只有三项：`TEXT_GENERATION`、`STRUCTURED_OUTPUT`、`VISION`。
- **四个后端都只会组装图片。**

  | 后端 | 支持范围 | 图片组装方式 |
  |---|---|---|
  | `gemini.py` | Gemini 两个 provider | 用 PIL 打开本地图，或把 URL 字符串直接放进 contents |
  | `openai.py` | OpenAI、百炼、MiniMax，以及自定义 `openai-chat` 端点 | `image_url` data URI |
  | `ark.py` | 火山方舟 | `image_url` |
  | `grok.py` | Grok | xai SDK 的 `image` |

- **能力声明与校验。** `lib/config/registry.py` 的 `ModelCapability` 是封闭词表，要求"新 token 先有消费方再入表"，因此不能提前加一个 `video_input` 占位。`lib/config/resolver.py` 的 `_ensure_text_model_vision_capable` 只校验 registry 内的模型，自定义供应商直接放行。
- **抽帧工具只有雏形。** `lib/infra/thumbnail.py` 已经封装了 ffmpeg/ffprobe 的首帧、尾帧、按帧号取帧，带子进程超时和原子写。可以在它的基础上做通用抽帧，但目前没有按 fps 或场景切换采样的函数。
- **Agent 看不了视频。** ArcReel Agent 运行在 Claude Agent SDK 上，通过 Read 工具能看图片，但看不了视频。如果 `ANTHROPIC_BASE_URL` 指向第三方 Anthropic 兼容端点，图片能力要看那个端点是否支持。

### 4.2 逐供应商对照

| provider（registry） | 已登记的文本模型与能力声明 | 今天就能用 | 厂商实际视频能力 | 缺什么 |
|---|---|---|---|---|
| `gemini-aistudio` / `gemini-vertex` | 3.1 Pro、3 Flash（声明 vision）；3.1 Flash Lite（未声明 vision） | 抽帧 + 多图 | **原生视频 + 音频** | 视频 Part（Files API 或内联上传，`video_metadata` 的 fps 和裁剪、`media_resolution`）；3.1 Flash Lite 实际支持多模态，但 registry 没声明 vision；没有 3.5/3.8 Flash |
| `ark` | Seed 2.0 Pro/Lite/Mini（260215，声明 vision）、Seed 1.8（声明 vision + structured） | 抽帧 + 多图 | 原生视频（`video_url` + fps）；260428 快照的 lite/mini 能理解音频 | ark 后端发 `video_url`；已登记的是 260215 快照，是否支持音频未确认；没有 Seed 2.1 |
| `ark-agent-plan` | Seed 2.0 三款（声明 vision）；DeepSeek、GLM、Kimi、MiniMax 为纯文本 | 抽帧 + 多图（仅 Seed） | 该订阅通道是否接受视频，未一手确认 | 同 ark |
| `dashscope` | qwen-plus、qwen3.6-plus、qwen3-max、qwen3.7-max、qwen3.6-flash、qwen-long，**全部未声明 vision** | **不能**（vision 校验会拒绝） | Qwen3.6-Plus 等原生支持视频（不含音频）；Omni 支持音视频 | 更正 vision 声明，或登记 VL/Omni 模型；openai 后端发 `video_url` + `fps`；本地文件的 Base64 上限只有 10 MB |
| `openai` | GPT-5.5 / 5.4 / 5.4 Mini / 5.4 Nano（声明 vision） | 抽帧 + 多图 | 无视频输入 | 只缺抽帧工具 |
| `grok` | Grok 4.20 / 4.1 Fast（声明 vision） | 抽帧 + 多图 | 无视频输入 | 只缺抽帧工具 |
| `minimax` | M3（声明 vision）、M2.7 | 抽帧 + 多图（M3） | M3 原生视频（fps [0.2, 5]） | openai 后端发 `video_url` |
| `agnes` | 2.0 Flash（纯文本） | 不能 | 未调研 | — |
| 自定义供应商（`openai-chat` / `gemini-generate`） | 没有逐模型的能力声明 | 抽帧 + 多图（视供应商而定） | 视供应商而定 | 同上 |

**小结**：今天不改代码，唯一可行的方案是"抽帧 + 多图"，在 Gemini、豆包、OpenAI、Grok、MiniMax-M3 这些声明了 vision 的模型上都能跑。但是：
- 仓库里没有 fps 采样和打时间码的工具，需要新写。
- 要走原生视频，需要在请求模型里加视频输入字段，并在 gemini、openai 兼容、ark 三个后端分别实现。
- 同时要补一个有真实消费方的视频能力 token。

---

## 5. 能力与成本对照表

"10 s 成本"只计输入，按标准档计算。美元价格未换算成人民币。

| 方案 / 模型 | 原生视频 | 音轨 | 采样控制 | 时间戳（厂商声明 / 实测） | 10 s 成本量级 | ArcReel 已接入 |
|---|---|---|---|---|---|---|
| Gemini 3.x Flash / Flash-Lite | ✅ | ✅ | fps 可调，可裁剪，可调分辨率 | 声明支持 `MM:SS`；2.5 Pro 的 R1@0.5 约 61 | $0.0003–0.002 | ✅（3 Flash / 3.1 Flash Lite，但无视频通道） |
| Gemini 3.1 Pro | ✅ | ✅ | 同上 | 同上 | ≈$0.002–0.006 | ✅（无视频通道） |
| Qwen3.x-Plus / Flash、Qwen3-VL | ✅ | ❌ | fps 0.1–10 | 声明"秒级定位"；Qwen3-VL 的 Charades mIoU 约 56–65（自报） | ¥0.001–0.09 | 部分（未声明 vision，无视频通道） |
| Qwen3.8-Omni-Flash | ✅ | ✅ | 约 2 fps（参数未确认） | 声明支持带时间戳的转写 | ≈¥0.005 | ❌ |
| 豆包 Seed 2.1 Pro | ✅ | ❌ | fps 0.2–5，最少 16 帧 | 每帧插入时间戳文本；Seed1.5-VL mIoU 约 64（自报） | ≈¥0.04 | ❌（已接入 2.0） |
| 豆包 Seed 2.0 Lite / Mini（260428） | ✅ | ✅ | 同上 | 同上 | ¥0.001–0.004 | 快照不同（已接入 260215） |
| MiniMax-M3 / GLM-4.6V / Kimi | ✅ | 未确认 | 部分可调 | GLM 声明支持时间段定位 | 未能计算 | M3 ✅（无视频通道） |
| Claude（抽帧） | ❌ | ❌ | 自己控制 | 取决于帧标注；GPT-5 的 R1@0.5 约 42，Claude 无数据 | $0.005–0.05（10 帧） | Agent 运行时自带 |
| OpenAI GPT-5.x（抽帧） | ❌ | ❌ | 自己控制 | 同上 | $0.0004–0.055（10 帧） | ✅ |
| 确定性信号（ffmpeg freezedetect / scene、OCR、ArcFace） | — | — | 逐帧 | **精确到帧** | 只有本地 CPU/GPU 开销 | ffmpeg 已是运行依赖（Docker 自带），OCR 和人脸模型未引入 |

---

## 6. 推荐审阅方案：混合

### 6.1 分层

1. **确定性预处理，结果直接给剪辑用。**
   - `ffprobe` 取时长、帧率、有无音轨。
   - `freezedetect` 和相邻帧差给出**首尾冗余帧、冻结段的精确起止**，这就是 L2 最需要的入出点。
   - 用 `select=scene` 找镜头切换点。
   - 可选：抽样帧跑 OCR，标记文字或水印出现的区间。
2. **MLLM 逐片段审阅，产出结构化判断和粗定位。**
   - 输入：片段本身（原生视频）或按第 2.3 节抽的帧、这个单元的视频提示词、角色参考图（用于检查一致性），以及第 1 层给出的候选入出点。
   - 输出 JSON：
     - 是否符合提示词，给 1–5 分并说明理由；
     - 缺陷列表，每项包含类型、`start`/`end` 秒、严重度、置信度；
     - 建议可用区间；
     - 如果有音轨，还要判断音画是否匹配。
   - 首选 Gemini 3.x Flash / Flash-Lite：原生视频、带音频、fps 可调，而且最便宜。
   - 国内网络环境下可选：
     - Qwen3.x-Plus / Flash：视频，无音频；
     - Qwen3.8-Omni-Flash：音视频；
     - Seed 2.0 Lite / Mini 260428：音视频。
   - 采样参数：审阅用 2–5 fps，分辨率用 low/medium。可疑片段再用 high 或更高 fps 复查。
3. **没有视频通道时的回退。**
   - 同一套提示词和 schema，换成"抽帧 + 显式时间码"输入。
   - OpenAI、Grok、Claude（Agent 用 Read 看帧）都能用。
   - 有音轨的片段需要另走 ASR。
4. **怎么用这些结论。**
   - MLLM 给出的缺陷判断只用来**排序、标记、给出建议剪点**，交给用户确认。
   - 不要仅凭一次 MLLM 判断就自动删掉素材或重新生成，因为召回和精度都不够（见第 3 节）。
   - 唯一例外是"动作与提示词不符"，可以作为较强的信号参与重生成建议。

### 6.2 前提条件

- **代码侧：**
  - 在请求模型里加视频输入（本地路径 + fps + 裁剪区间）。
  - gemini、openai 兼容、ark 三个后端分别实现：Gemini 用 Part + `video_metadata`，超过内联上限走 Files API；百炼、方舟、MiniMax 用 `video_url` + `fps`，这是非标准的 OpenAI 扩展字段。
  - 新增一个视频输入能力 token，并同时实现它的消费方（resolver 校验）。
  - 抽帧工具（均匀采样、场景切换、首尾加密、打时间码）可以从 `lib/infra/thumbnail.py` 扩展出来。
- **登记侧：**
  - 核实并更正百炼 Qwen3.6-Plus 等原生多模态模型的 `vision` 声明，以及 Gemini 3.1 Flash Lite 的声明。
  - 视需要登记 Omni 模型，或支持音频的 Seed 快照。
- **传输侧：**
  - ArcReel 的片段存在本地，一般没有公网 URL。
  - Base64 上限：百炼 10 MB，方舟和 MiniMax 50 MB，Gemini 内联以 20 MB 为稳妥上限。1080p 高码率片段可能超限，需要先转码降码率，或者走各家的文件上传接口。
- **评估侧：**
  - 上线前用 ArcReel 自己生成的片段做小样本人工标注，测召回率和时间偏差，决定哪些缺陷类型可以信任 MLLM。
  - Gemini 的 token 口径以实测 usage 为准。
- **成本侧：**
  - 一集按 60 个片段计，原生视频审阅一轮约 $0.02–0.4（Gemini）或 ¥0.1–5（国内）。
  - 抽帧走 Claude Sonnet 或 GPT-5.5 高分辨率（每片段 10 帧），约 $1.4–3.3 每集；按 2 fps 抽帧则再翻倍。
  - 相对视频生成本身的费用可以忽略，但抽帧方案的 token 量高一个数量级。

### 6.3 未解问题

- Seed 2.0 的 260215 快照是否理解音频；Qwen-Omni 用户侧 fps 参数的名称和范围；GLM、Kimi、MiniMax 的视频时长上限、token 公式和音频支持。
- Gemini 视频理解页与 media-resolution 页的 token 口径冲突。
- 没有任何公开基准衡量 Claude 对生成视频缺陷的定位能力；首尾冗余帧、水印这两类没有 MLLM 基准。
- 各家时间戳在 ArcReel 实际片段上的偏差量级，只能靠自测得出。
