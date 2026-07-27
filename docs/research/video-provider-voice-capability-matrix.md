> 来源: GitHub issue #ArcReel/ArcReel#1380

# 视频供应商"人物声音一致性"能力调研（除 Seedance 外）

**调研日期**：2026-07-27
**用途**：产品/工程决策输入，评估本仓库已接入的视频供应商（seedance/Ark 除外，由另一票单独覆盖）是否具备原生的"人物声音一致性"手段——音频参考/音色指定/voice ID 输入、prompt 层声音描述支持度、音轨开关与计价形态
**范围**：kling（可灵）/ vidu / gemini-veo / minimax（海螺）/ dashscope（万相/HappyHorse）/ openai-sora / grok / newapi / agnes，共 9 家
**信息来源与可信度声明**：每条结论均标注来源类型（官方文档 / 官方博客/公告 / 官方论坛-非官方回复 / 社区实证）。官方文档缺失处一律写"无公开资料"，不用训练知识或常识推测补全。部分厂商官方站点对本环境的自动抓取工具返回 446/403（反爬/地域限制），已改用可行的镜像路径获取原始 HTML 后核实；仍不可达处已在正文标注。

---

## 1. 可灵 Kling

**代码现状**：仅 `kling-v2-6`（pro 档）声明 `generate_audio=True`；`kling-v2-5-turbo` / `kling-v3` / `kling-v3-omni` / `kling-video-o1` 均 `generate_audio=False`。

### 1.1 音频参考 / 音色指定 / voice ID 输入能力

**有，且比代码当前的单一布尔位表达的能力更丰富**。可灵官方在两条产品线上各提供了一套音色克隆/绑定机制：

- **Kling Video 2.6 "Voice Control"**：官方使用指南明确说明可上传 5–30 秒纯净人声音频创建自定义音色（单账号最多 200 个），并通过 `[角色名] @音色名` 的 prompt 语法把音色绑定到具体角色台词上，例如 `[Livestream Host] @Sweet Female Voice: "This top is a trending must-have!"`。来源（官方文档）：[Kling Video 2.6 Audio User Guide](https://kling.ai/quickstart/klingai-video-26-audio-user-guide)
- **Kling 3.0 / 3.0 Omni "Custom Voice"（Voice Management）API**：独立的音色管理接口族——`POST /v1/general/custom-voices`（用 `voice_url` 音频文件或已生成视频的 `video_id` 创建音色）、`GET .../custom-voices/{id}`、`GET .../custom-voices`（分页列表）、`GET .../presets-voices`（官方预设音色库）、`POST .../delete-voices`。音频要求"clean and free of noise, with only one type of human voice present, with a duration of no less than 5 seconds and no longer than 30 seconds"。来源（官方文档）：[Voice Management](https://kling.ai/document-api/api/video/3-0-omni/voice-customization)

两套机制都属于严格意义上的"音色 ID 输入 / 声音克隆"能力，非单纯的 prompt 文字描述。

### 1.2 prompt 层声音描述支持度

**官方明确支持，且给出结构化写法**。v2.6 官方指南给出角色描述内嵌语气/情绪属性的写法：`[Caucasian beauty influencer, sweet and fresh voice]`、`[African-American male reporter, steady voice]`，并推荐描述词汇：deep / gentle / hoarse / clear / crisp / raspy / cheerful / lively 等。来源（官方文档）：同上 [Voice User Guide](https://kling.ai/quickstart/klingai-video-26-audio-user-guide)

### 1.3 音轨开关机制与计价

- **v2.6**：`audio`（Native Audio Toggle）为独立开关。开启后生成"lip-synced dialogue, sound effects, and ambient sounds"（含对话、音效、环境音，非仅 BGM）。计价：Native Audio ON = **10 积分/秒**（Professional 档）；额外启用 Voice Control（自定义音色绑定）再加 **2 积分/秒**（订阅用户免费）；Native Audio OFF = Professional 5 积分/秒 / Standard 3 积分/秒。来源（官方文档）：同上。
- **v3 / v3-omni**：`audio` 参数为 enum `native` / `off`，默认 `off`。Kling 官方文档（英文版）仅泛泛说明 "native" = "generated video includes native audio matching the visuals"，**未明确音频内容是否含对话**。但阿里云百炼代理的可灵接口文档对同一批模型（`kling-v3-video-generation` / `kling-v3-omni-video-generation`）给出了更明确的中文限定："开启后模型将根据视频内容自动生成匹配的**背景音乐或音效**"——即明确排除对话人声。来源（官方文档，阿里云代理）：[可灵Kling视频生成API文档](https://help.aliyun.com/zh/model-studio/kling-video-generation-api-reference/)。两处官方文档表述不完全一致（kling.ai 英文文档未明说排除对话，阿里代理中文文档明说仅 BGM/音效），建议以更明确的阿里代理文档为准。
- 另有独立的 **"Video to Audio"** 后处理 API（对已有视频二次配音），入参为 `sound_effect_prompt` + `bgm_prompt` + `asmr_mode`，**无对话/台词参数**，输出仅为音效与 BGM——进一步印证"原生生成音频"与"后处理配音"两条线里，只有 v2.6 的原生生成、Voice Control 通道能产出可控人声对话。来源（官方文档）：[Video to Audio](https://kling.ai/document-api/apiReference/model/videoToAudio)

### 1.4 与代码声明的一致性

**基本一致，但代码的能力粒度落后于官方**。代码把"人声"能力压缩成单一 `generate_audio` 布尔位，仅在 `kling-v2-6` 上置 True——这与官方"只有 v2.6 的 Native Audio + Voice Control 才能产出可控对话人声，v3/v3-omni 的 audio 参数仅 BGM/音效"的实际情况方向一致。但代码完全没有表达官方已存在的**音色 ID / 声音克隆输入**这一独立维度（v2.6 的 Voice Control 与 v3.0/3.0 Omni 的 Custom Voice API），这是一个明显的能力空白点，而非"过期"——是从未被建模。

---

## 2. Vidu

**代码现状**：仅 viduq3 系列（viduq3-pro/turbo/pro-fast/viduq3/viduq3-mix）请求体带 `audio: bool` 开关；viduq2/viduq1/vidu2.0 无此参数。

### 2.1 音频参考 / 音色指定 / voice ID 输入能力

**视频生成接口本身无此能力**。Vidu 官方文本生视频 API 文档中未见 voice ID / 音色克隆 / 音频参考输入参数。平台另有独立的 "Voice Clone" 与 "Text To Speech" 端点（归类在 Audio Generating 分类下），**但未与视频生成接口整合**，即无法用克隆出的音色驱动视频里的角色说话。来源（官方文档）：[platform.vidu.com/docs/text-to-video](https://platform.vidu.com/docs/text-to-video)

### 2.2 prompt 层声音描述支持度

**官方 API 文档未给出声音描述的专项写法指引**（仅说明 prompt 最大长度 5000 字符）。社区实证（评测/自媒体，非官方文档）显示 Vidu 官方营销与"Super Voice Actor"概念文章建议将音效、BGM 描述放在 prompt 末尾，可影响角色情绪化台词与口型匹配：
- （社区评测）[为剧而生！Vidu Q3参考生视频实测](https://www.163.com/dy/article/KQIP270G0556LD7I.html)
- （官方产品页，非 API 文档）[Vidu Q3 AI Video Model with Native Audio](https://www.vidu.com/vidu-q3)

### 2.3 音轨开关机制与计价

官方文档确认：`audio`（布尔，可选）参数——"Whether to use direct audio-video generation capability. Default: **true**"，且仅 **viduq3-pro / viduq3-turbo** 等 viduq3 系列模型支持，viduq2/viduq1/vidu2.0 不支持该参数。开启后输出 "video with sound (**including dialogue and sound effects**)"——官方明确音轨含对话人声，非仅音效。另有独立 `bgm` 参数（默认 false），但文档标注 q3 系列不支持该参数。**定价影响文档未披露**具体差价，仅引用独立 Pricing 页面。来源（官方文档）：[platform.vidu.com/docs/text-to-video](https://platform.vidu.com/docs/text-to-video)

### 2.4 与代码声明的一致性

**一致**。代码"仅 viduq3 系列声明音频能力，旧模型无此参数"与官方文档描述完全吻合，未发现过期点。

---

## 3. gemini/veo

**代码现状**：仅 Vertex AI 访问方式声明音频能力位；AI Studio 访问方式代码里假定恒有声但未声明能力位。

### 3.1 音频参考 / 音色指定 / voice ID 输入能力

**无公开资料确认存在 API 层面的音色/voice ID 输入**。官方文档（Gemini API / Vertex AI 的 Veo 页面）仅描述"native audio"为通用能力（对话、环境音、音效随视频同步生成），未见任何 voice ID、音频参考、声音克隆相关参数。来源（官方文档）：[Generate videos with Veo 3.1 in Gemini API](https://ai.google.dev/gemini-api/docs/veo)、[Video generation in the Gemini API](https://ai.google.dev/gemini-api/docs/video)。另有 Google 支持社区帖子提到"DeepMind 页面说可以在 Veo 中使用自己的声音"，但该说法来自消费端产品（Gemini App / Flow）宣传，且社区用户反馈不清楚如何在实际产品中触达该功能——**不构成已确认的 API 能力**，来源类型：社区/官方支持论坛（非官方明确文档）：[Gemini Apps Community 讨论帖](https://support.google.com/gemini/thread/364265786)。

### 3.2 prompt 层声音描述支持度

官方文档承认 Veo 3.1 能"generate richer native audio, from natural conversations to synchronized sound effects"，隐含 prompt 中的对话/声音描述会被模型采纳，但**未给出类似可灵那样的结构化声音描述写法指引**。来源（官方博客）：[Introducing Veo 3.1 and new creative capabilities in the Gemini API](https://developers.googleblog.com/introducing-veo-3-1-and-new-creative-capabilities-in-the-gemini-api/)

### 3.3 音轨开关机制与计价 / AI Studio vs Vertex AI 差异

- Vertex AI 侧确有 `generate_audio`（`GenerateVideosConfig` 的字段）参数，社区反馈显示该参数在部分模型/路径下**存在实现问题**：有开发者反馈"通过 Vertex AI 调用 veo-3.1-generate-001 时设置 `generate_audio=False` 会报错"，且另一位社区用户明确回复"audio generation is built into Veo 3...There is no off switch"——即**目前无法通过该参数真正关闭音频**。这是 Google AI 官方开发者论坛的用户讨论帖，非 Google 官方声明，来源类型：官方论坛-非官方回复：[Veo 3 API - generate_audio parameter not supported](https://discuss.ai.google.dev/t/veo-3-api-generate-audio-parameter-not-supported-last-frame-limitations/119206)
- 官方文档未明确区分 "AI Studio 访问方式" 与 "Vertex AI 访问方式" 在音频能力位上的差异声明；两条访问路径的官方页面都笼统宣称模型"natively generates audio"，但没有一处显式说明 AI Studio 路径下 `generateAudio` 参数是否可配置。**该点无公开资料能明确证实或证伪**代码里"AI Studio 恒有声不声明能力位、Vertex AI 才声明能力位"的历史区分。
- 定价：**未查到官方文档中"开启/关闭音频"对应的差异化定价条目**；官方定价页按分辨率/时长计费，未见音频独立计费项——但由于目前观测到的现象是"音频事实上无法关闭"，定价文档也未单列音频档位，这与"音频恒定捆绑、不单独计费"的判断相符。

### 3.4 与代码声明的一致性

**方向大体自洽，细节缺公开资料验证**。代码假设"Vertex AI 声明能力位、AI Studio 假定恒有声但不声明"，与目前查到的社区证据（AI Studio/Gemini API 路径下 `generate_audio` 参数实际不可用、恒有声）方向一致；但由于官方文档本身未把这个区分写清楚，**无法给出"是否过期"的确定结论**，只能说现有代码假设与最新一手可查证据（含论坛证据）不矛盾。

---

## 4. minimax（海螺 Hailuo）

**代码现状**：完全不声明音频能力（Hailuo 2.3 / 2.3-Fast / S2V-01 均未声明）。

### 4.1 音频参考 / 音色指定 / voice ID 输入能力

**无**。MiniMax 官方视频生成文档（Video Generation / T2V / I2V / Video Agent 端点）未提及任何音频、语音、声音相关的输入参数或输出字段。MiniMax 平台确有独立的语音克隆/TTS 能力（"MiniMax Speech"、300+ 系统音色、自定义克隆音色），但这是**独立的文本转语音产品线**，与视频生成接口完全分离，无法把克隆音色接入视频角色。来源（官方文档）：[Video Generation - MiniMax API Docs](https://platform.minimax.io/docs/guides/video-generation)、[MiniMax Speech 2.8 官方博客](https://www.minimax.io/news/minimax-speech-28)

### 4.2 prompt 层声音描述支持度

**无公开资料**——官方视频生成文档未提及声音/对话描述写法，官方也未在任何 Hailuo 相关文档或新闻稿中说明 prompt 可以描述人声/音色。

### 4.3 音轨开关机制与计价

**官方文档完全没有出现"audio"相关字段**，Hailuo 2.3 官方新闻稿也仅描述画面（角色动作、表情、风格化、光影）能力升级，未提及音频。来源（官方博客）：[MiniMax Hailuo 2.3: A New Level of Complex Video Performance & Media Agent](https://www.minimax.io/news/minimax-hailuo-23)。社区实证（评测网站，非官方）明确指出 MiniMax/Hailuo 是"visual-only model"，输出为静音视频，需另配 TTS/音乐模型：来源类型：社区评测：[WaveSpeedAI 集合页](https://wavespeed.ai/collections/minimax)。S2V-01 同理——官方新闻稿只强调"单图驱动的角色一致性"（人脸/身份一致），未提音频；社区资料确认其输出为静音视频。来源（官方博客）：[S2V-01: Subject Reference of Hailuo](https://www.minimax.io/news/s2v-01-release)

### 4.4 与代码声明的一致性

**一致**。官方文档与社区实证均未见 Hailuo 系列（2.3/2.3-Fast/S2V-01）具备原生音频能力，代码"完全不声明"与事实相符，未发现过期点。

---

## 5. dashscope（万相 / HappyHorse）

**代码现状**：所有已注册模型（`happyhorse-1.0-{t2v,i2v,r2v}`、`wan2.7-{t2v,i2v,r2v}`）均声明音频能力，代码注释称"音频恒开（无开关参数）"。

### 5.1 音频参考 / 音色指定 / voice ID 输入能力

**Wan（万相）系列有，HappyHorse 系列无**：

- **Wan2.7 参考生视频（R2V）**：`reference_voice` 参数——"用于指定参考素材（图像/视频）中主体角色的音色"，支持公网 URL 或临时 OSS URL 传入 1–10 秒的 WAV/MP3 音频（≤15MB）；若同时传入含音频的 `reference_video` 与 `reference_voice`，`reference_voice` 的音色优先，覆盖视频原声。这是严格意义上的音色一致性输入。来源（官方文档）：[万相2.7-参考生视频-API参考](https://help.aliyun.com/zh/model-studio/wan-video-to-video-api-reference)
- **Wan2.7 文生视频**：`audio_url`（可选）——传入自定义音频文件（旁白/配乐）替代自动配音；不传则模型"根据视频内容自动生成匹配的背景音乐或音效"。来源（官方文档）：[万相2.7文生视频API参考](https://help.aliyun.com/zh/model-studio/text-to-video-api-reference)
- **HappyHorse 系列（t2v/i2v/r2v）**：官方 API 参考文档中**完全没有出现**任何音频/audio/reference_voice 相关参数（已逐字核对文生视频、图生视频、参考生视频三份 API 参考文档，均无相关字段），来源（官方文档）：[HappyHorse-文生视频API参考](https://help.aliyun.com/zh/model-studio/happyhorse-text-to-video-api-reference)、[HappyHorse-图生视频-基于首帧API参考](https://help.aliyun.com/zh/model-studio/happyhorse-image-to-video-api-reference)、[HappyHorse参考生视频API参考](https://help.aliyun.com/zh/model-studio/happyhorse-reference-to-video-api-reference)。但阿里云"视频生成与编辑"模型总览页把 HappyHorse 1.0/1.1 系列标注为"**有声视频**"——即音频是模型内生能力、无需参数开关，这与 API 参考文档"无 audio 参数"并不矛盾（恒开、不可关闭、不可自定义）。来源（官方文档）：[视频生成与编辑模型概览](https://help.aliyun.com/zh/model-studio/video-generate-edit-model/)

### 5.2 prompt 层声音描述支持度

**官方明确支持，且给出结构化公式**，适用于"文生视频/图生视频"（覆盖 Wan 与 HappyHorse 使用的同一套 prompt 体系）：官方"文生视频或图生视频提示词 Prompt 使用指南"给出公式——**提示词 = 主体 + 场景 + 运动 + 声音描述（人声/音效/背景音乐）**，其中：
- 人声 = 角色说话的内容 + 情绪 + 语调 + 语速 + **音色** + 口音
- 音效 = 音源材质 + 行为 + 环境音
- 背景音乐 = 背景音乐/配乐 + 风格

官方给出示例："一个男人在讲脱口秀，他说道：'好好学习，天天向上'，语气轻松，语速适中，声音清亮，美式英文。" 该指南明确这是"指导声音内容和声音氛围"的**文字描述层面**能力，未提及跨视频音色一致性保障或 voice ID 绑定机制——即声音描述可控生成效果，但不保证同一"音色"在不同调用间可复现（这正是 `reference_voice` 参数存在的意义：真正的跨调用音色一致性要靠音频参考，而非纯文字描述）。来源（官方文档）：[文生视频或图生视频提示词Prompt使用指南](https://help.aliyun.com/zh/model-studio/text-to-video-prompt)

### 5.3 音轨开关机制与计价

- **HappyHorse**：官方总览页标注"有声视频"，API 参考文档中**无 audio 开关参数**，即恒开、不可关闭。定价按 `output_video_duration`（时长）+ `SR`（分辨率档 720P/1080P）计费（720P ¥0.9/s、1080P ¥1.6/s，见[官方定价文章](https://developer.aliyun.com/article/1731470)），**未见音频单独计费项**——即音频不额外加价。社区技术解析（非官方 API 文档，但来自阿里云开发者社区）补充说明音频内容含"台词对话、环境音与拟音效果"，一次前向推理同步生成，来源类型：社区/开发者社区文章：[HappyHorse完全指南](https://developer.aliyun.com/article/1731646)
- **Wan2.7**：官方文档说明"wan2.7 默认生成有声视频，无需设置"（对比 wan2.6 需要显式 `audio: true/false`），即 wan2.7 相比上一代取消了显式开关、音频默认开启，但可通过传入 `audio_url` 自定义或留空自动生成；**未见关闭音频的参数**。定价依据 `duration` + `resolution`，**文档未列音频独立计费项**。来源（官方文档）：[视频生成与编辑模型概览](https://help.aliyun.com/zh/model-studio/video-generate-edit-model/)、[万相2.7文生视频API参考](https://help.aliyun.com/zh/model-studio/text-to-video-api-reference)

### 5.4 与代码声明的一致性

**基本一致**。代码"所有已注册模型均声明音频能力，音频恒开无开关参数"与官方文档吻合——HappyHorse 与 Wan2.7 官方 API 参考文档确实都没有一个"可关闭音频"的参数。但代码把 Wan 系列的 `reference_voice`（音色一致性输入）这一独立能力维度**完全未建模**——与 Kling 类似，这是能力空白而非声明过期，值得补充。

---

## 6. openai-sora（Sora 2）

**代码现状**：完全不声明音频能力。

### 6.1 音频参考 / 音色指定 / voice ID 输入能力

**无公开资料确认存在**。OpenAI 官方 Sora 2 模型页面（Models 目录）与官方发布文章均未提及 voice ID、音色克隆或音频参考输入参数。来源（官方文档）：[Sora 2 Model | OpenAI API](https://platform.openai.com/docs/models/sora-2)、（官方发布文章）[Sora 2 is here](https://openai.com/index/sora-2/)

### 6.2 prompt 层声音描述支持度

官方发布文章确认可通过自然语言 prompt 引导对话内容，"videos come with audio that matches the video content by default, and users can direct the audio using the prompt and even include dialogs"；但**未见官方给出类似可灵那样的结构化声音属性描述写法**（音色/语调等专项指引）。来源（官方发布文章）：[Sora 2 is here](https://openai.com/index/sora-2/)

### 6.3 音轨开关机制与计价

**音频为核心内置能力，非可选开关**。官方模型页面把 "Audio" 列为 Sora 2 的 Output modalities 之一，未见任何禁用/关闭音频的参数说明。定价按秒计费的单一费率（Sora 2：约 $0.10/秒；Sora 2 Pro 720p/1080p 更高档位），**未见音频独立计费项或"关闭音频"降价选项**——即音频与视频捆绑计费。来源（官方文档）：[Sora 2 Model | OpenAI API](https://platform.openai.com/docs/models/sora-2)。官方发布文章进一步说明音频生成是与视频同一次前向推理同步产出（"native audio generation, where the model creates sound simultaneously with video, not as a separate step"），内容涵盖对话（含唇形匹配）、环境声、音效："Sora 2 can generate dialogue (speech with timing that matches visible lip movements), ambient soundscapes, and sound effects aligned to on-screen events"。来源（官方发布文章）：[Sora 2 is here](https://openai.com/index/sora-2/)

### 6.4 与代码声明的一致性

**代码声明已过期**。官方文档与发布文章明确 Sora 2 原生同步生成含对话人声、唇形同步的完整音轨，且音频默认内置、无独立开关、按统一费率与视频捆绑计费——这是 Sora 2 发布时的核心卖点之一。代码目前完全不声明音频能力，与官方能力描述存在明显落差。

---

## 7. grok（xAI Grok Imagine Video）

**代码现状**：不声明音频能力位。

### 7.1 音频参考 / 音色指定 / voice ID 输入能力

**无公开资料确认存在**。查阅 xAI 官方文档（`docs.x.ai` 的 Imagine Overview 页、Video Generation 页）与官方新闻稿（`x.ai/api/imagine`、`x.ai/news/grok-imagine-api`），均未提及 voice ID、音色克隆或音频参考输入参数；`docs.x.ai/developers/model-capabilities/video/generation` 页面仅记录 `prompt`、`image_urls`、`duration` 等视觉相关参数，无任何音频字段。来源（官方文档）：[Video Generation - xAI Docs](https://docs.x.ai/developers/model-capabilities/video/generation)

### 7.2 prompt 层声音描述支持度

**无公开资料**——官方文档未给出声音/对话描述的 prompt 写法指引。第三方评测/博客（社区来源）描述 Grok Imagine Video 1.5 "sound effects, ambient audio, music, and lip-synced dialogue generate in the same pass as the picture"，但这不是官方文档原文，来源类型：社区/第三方博客：[Grok Imagine Video 1.5: xAI's Image-to-Video Model With Native Audio](https://wavespeed.ai/blog/posts/grok-imagine-video-1-5-image-to-video-api/)

### 7.3 音轨开关机制与计价

**音频为默认内置能力，不单独计费**。官方模型文档（`docs.x.ai/developers/models/grok-imagine-video-1.5`）给出按分辨率分档的秒级定价：480p $0.08/秒、720p $0.14/秒、1080p $0.25/秒，**未见独立的音频开关参数**。第三方渠道整理的官方计费口径显示"Audio is included in every API generation at no additional charge"——即音频恒定捆绑在视频生成费用内，无需额外付费，也无法单独关闭；因未在 `docs.x.ai` 原始页面中直接看到这句英文原文（页面结构未完整暴露该说明），此处该具体措辞标注为来源类型：社区/第三方计费说明汇总，官方定价数字本身（$0.08/0.14/0.25 每秒）已通过官方文档核实：[Grok Imagine Video 1.5 | xAI Docs](https://docs.x.ai/developers/models/grok-imagine-video-1.5)

### 7.4 与代码声明的一致性

**代码声明可能已过期**。官方文档与多个第三方评测一致确认 Grok Imagine Video（当前 1.5 版本）原生同步生成含唇形同步对话、音效、音乐的完整音轨，且音频默认捆绑不单独计费。代码目前不声明音频能力位，与官方能力描述存在落差，但由于 `docs.x.ai` 官方页面对"音频内容具体含对话"这一点未给出逐字确认（多次 WebFetch 仅拿到定价数字，未拿到音频内容说明原文），建议按"高度疑似过期，但音频内容细节仍需人工登录 x.ai 开发者控制台复核"处理。

---

## 8. newapi（多厂商中转）

**代码现状**：完全不声明音频能力。

**结论**：newapi（`QuantumNous/new-api`）本身是一个开源自托管的 AI 模型聚合/中转网关，把 OpenAI、Claude、Gemini 及其他第三方模型的调用格式转换为统一接口，**并非自研模型供应商**，因此不存在"newapi 官方的音频能力文档"这一概念——中转场景下，音频能力（音轨开关、音色输入、prompt 声音描述支持度）完全取决于被中转的上游具体模型（如 Sora / Kling / 即梦 / Wan / Veo 等），而这些模型各自的官方音频能力已分别在第 1/3/5/6 节调研。代码"完全不声明音频能力"对 newapi 网关本身而言是合理的默认——网关层不应擅自假设某个上游模型必然有声或无声，应逐上游模型透传/映射。来源（项目仓库）：[GitHub - QuantumNous/new-api](https://github.com/QuantumNous/new-api)

### 与代码声明的一致性

**一致，且是网关型供应商的合理默认**。此结论不适用"过期"判断——newapi 没有自身的音频能力可供核实，代码不声明是正确的架构选择而非遗漏。

---

## 9. agnes（Agnes AI / SapiensAI）

**代码现状**：完全不声明音频能力，代码注释称"Agnes 视频无音频能力"。

### 9.1 Agnes 是官方渠道还是转售/中转性质供应商？

**证据不足以下定论，但有若干值得注意的信号**。Agnes AI 由新加坡公司 SapiensAI（由 Bruce Yang 于 2025 年初创立）运营，官方自我定位为"developed and trained entirely in Singapore"的第一方模型（对标"新加坡版 DeepSeek"），宣称"reducing dependency on third-party foundational models from giants like OpenAI"——即官方对外口径是**自研、非转售**。来源类型：官方公关稿（PR Newswire，企业发布，非独立技术文档）：[SapiensAI Launches Agnes](https://www.prnewswire.com/apac/news-releases/sapiensai-launches-agnes--singapores-homegrown-answer-to-deepseek-302443885.html)

但以下几点在官方文档与 GitHub 目录中均**未获得技术层面的证实或证伪**：
- Agnes 官方文档（`agnes-ai.com/en/docs/overview`、`agnes-video-v12` 模型页）与官方模型目录仓库（`AgnesAI-Labs/AgnesAI-Models`）均未披露视频模型的底层技术栈或训练细节，只给出"OpenAI 兼容接口"的调用格式说明。来源（官方文档）：[Agnes-Ai Docs Overview](https://agnes-ai.com/en/docs/overview)、[GitHub - AgnesAI-Labs/AgnesAI-Models](https://github.com/AgnesAI-Labs/AgnesAI-Models)
- 平台以"免费 API Key、无限量额度、统一网关"作为主打卖点——这一模式与"自研前沿视频大模型需要巨额训练/推理算力"存在一定张力，但**不构成技术层面的转售证据**，仅是商业模式上的可疑信号，不应作为结论依据。
- 未查到任何独立技术评测/逆向分析文章明确指出 Agnes 视频模型底层复用了 Kling/Sora/Vidu/Wan 等第三方模型。

**结论：无法确认或证伪"官方 vs 转售"这一问题，官方口径自称第一方自研，缺乏独立技术验证。**

### 9.2 音频参考 / 音色指定 / voice ID 输入能力

**无公开资料确认存在**具体的 API 参数。官方文档营销文案层面提到"Supports generating high-quality video content and synchronized audio-visual output"、"synchronized audio-video generation"，但这些表述出现在产品概览的宣传性段落，**具体 API 参数文档（`agnes-video-v12` 等模型页）未给出任何音频相关的输入参数说明**——即官方文档在"营销语言"与"技术参数"两个层级上存在不一致：概览页暗示有音视频同步能力，具体模型 API 参考却完全没有音频字段。来源（官方文档）：[Agnes-Ai Docs Overview](https://agnes-ai.com/en/docs/overview)

### 9.3 prompt 层声音描述支持度

**无公开资料**。

### 9.4 音轨开关机制与计价

**无公开资料确认存在独立音频开关或音频计价条目**。已核实的模型参考文档（`agnes-video-v12`）未见 audio 参数或输出字段说明。

### 9.5 与代码声明的一致性

**基本一致，但官方文档本身存在营销语言与技术文档的内部矛盾**。代码注释"Agnes 视频无音频能力"与具体 API 参考文档（无音频字段）相符；但 Agnes 官方产品概览页确实使用了"synchronized audio-visual output"这类措辞，若未来该能力真正落地到具体模型的 API 参数中，代码声明会需要重新核实——目前判断为未过期，是营销先行、技术文档滞后的常见现象。

---

## 10. 汇总能力矩阵

| Provider | 音频开关参数 | 音轨内容类型 | 音色/voice 指定输入 | prompt 声音描述官方支持度 | 计价形态 | 与代码声明是否一致 | 主要出处 |
|---|---|---|---|---|---|---|---|
| **kling** | v2.6：`audio` 布尔开关（Native Audio Toggle）；v3/v3-omni：`audio` enum `native`/`off`（默认 off） | v2.6：**人声对话**+音效+环境音（lip-synced）；v3/v3-omni：仅**音效/BGM**（阿里代理文档明确限定，kling.ai 英文文档未明说） | **有**——v2.6 "Voice Control"（`@音色名` 绑定）+ 3.0/3.0 Omni "Custom Voice" API（`voice_url`/`video_id` 建声，最多 200 个） | 官方给出结构化写法（角色描述内嵌语气/情绪词） | v2.6：ON 10 积分/秒（pro）、Voice Control 额外 +2 积分/秒，OFF 5(pro)/3(std) 积分/秒；v3/v3-omni：定价未在已查页面披露 | 方向一致，但代码未建模音色 ID 这一独立维度 | [Kling Voice User Guide](https://kling.ai/quickstart/klingai-video-26-audio-user-guide)、[Voice Management](https://kling.ai/document-api/api/video/3-0-omni/voice-customization)、[阿里代理文档](https://help.aliyun.com/zh/model-studio/kling-video-generation-api-reference/) |
| **vidu** | 仅 viduq3 系列 `audio` 布尔（默认 true）；viduq2/q1/2.0 无此参数 | **人声对话**+音效（官方原文 "including dialogue and sound effects"） | **无**（视频接口内无；平台有独立 Voice Clone/TTS 端点但未整合进视频生成） | 无官方专项写法指引；社区实证建议 prompt 末尾加声音描述 | 官方未披露 audio 开/关的具体差价 | **一致** | [platform.vidu.com/docs/text-to-video](https://platform.vidu.com/docs/text-to-video) |
| **gemini-veo** | Vertex AI 侧 `generateAudio` 参数存在但社区反馈实际**无法关闭**（"no off switch"）；AI Studio 路径官方未明确声明该参数可配置 | 官方泛称"natural conversations to synchronized sound effects"（含对话），未细分是否可控 | 无公开资料确认 API 层面存在；消费端产品（Gemini App/Flow）营销提及"用自己的声音"，社区反馈无法确认如何触达，不构成已确认能力 | 官方承认 prompt 影响音频生成效果，但无结构化写法指引 | 官方定价未见音频独立计费项；因音频实际恒开，与"不可关闭故不单独计价"的观察相符 | 方向自洽但官方文档未明写 AI Studio/Vertex AI 差异，无法完全证实/证伪 | [Gemini API Veo 文档](https://ai.google.dev/gemini-api/docs/veo)、[开发者论坛讨论帖](https://discuss.ai.google.dev/t/veo-3-api-generate-audio-parameter-not-supported-last-frame-limitations/119206) |
| **minimax** | 无 | 无音频（静音视频，社区实证确认） | 无（独立 TTS/声音克隆产品线与视频生成不整合） | 无公开资料 | 定价与音频无关 | **一致** | [Video Generation API Docs](https://platform.minimax.io/docs/guides/video-generation)、[Hailuo 2.3 官方博客](https://www.minimax.io/news/minimax-hailuo-23) |
| **dashscope（Wan/HappyHorse）** | HappyHorse：无开关参数，官方总览页标注"有声视频"（恒开）；Wan2.7：无显式开关，默认有声，可选 `audio_url` 自定义音频 | 官方/社区资料显示含**对话台词**+环境音+拟音（HappyHorse），Wan 侧文档明确表述为"背景音乐或音效"（未强调对话，但 `reference_voice` 面向的是"音色"，隐含对话场景） | Wan2.7 **有**——`reference_voice` 参数（音频 URL，1–10s，覆盖参考视频原声），实现音色一致性；HappyHorse **无** | 官方给出结构化公式（人声=内容+情绪+语调+语速+音色+口音；音效=材质+行为+环境音；BGM=配乐+风格） | 均按时长+分辨率计费，未见音频独立计费项，音频不额外加价 | **基本一致**，但 Wan 的 `reference_voice` 音色一致性维度代码未建模 | [文生视频/图生视频Prompt指南](https://help.aliyun.com/zh/model-studio/text-to-video-prompt)、[万相2.7参考生视频API参考](https://help.aliyun.com/zh/model-studio/wan-video-to-video-api-reference)、[视频生成与编辑模型概览](https://help.aliyun.com/zh/model-studio/video-generate-edit-model/) |
| **openai-sora** | 无独立开关，音频默认内置为核心能力 | **人声对话**（含唇形匹配）+环境声+音效，官方发布文章明确 | 无公开资料确认 | 官方确认可用 prompt 引导对话内容，无结构化写法指引 | 单一秒费率（Sora 2 约 $0.10/秒），音频与视频捆绑计费，无独立音频计价 | **代码声明已过期**（代码不声明音频，但 Sora 2 官方音频是核心卖点） | [Sora 2 Model 官方文档](https://platform.openai.com/docs/models/sora-2)、[Sora 2 官方发布文章](https://openai.com/index/sora-2/) |
| **grok** | 官方文档未见独立开关参数，音频疑似默认内置 | 第三方评测称含唇形同步对话+音效+音乐（非官方文档原文） | 无公开资料确认 | 无官方专项写法指引 | 官方按分辨率分档秒费率（480p $0.08、720p $0.14、1080p $0.25），音频计价条款未在官方页面逐字确认（第三方渠道称音频免费捆绑） | **代码声明疑似过期**，但音频内容细节仍需人工复核官方控制台 | [Grok Imagine Video 1.5 官方文档](https://docs.x.ai/developers/models/grok-imagine-video-1.5)、[Video Generation - xAI Docs](https://docs.x.ai/developers/model-capabilities/video/generation) |
| **newapi** | 不适用（网关本身无模型，音频能力取决于被中转的上游模型） | 不适用 | 不适用 | 不适用 | 不适用 | **一致**（网关层合理默认，非遗漏） | [GitHub - QuantumNous/new-api](https://github.com/QuantumNous/new-api) |
| **agnes** | 无公开资料确认存在音频参数（具体模型 API 文档无音频字段） | 无公开资料（概览页营销文案提及"同步音视频"但技术文档未落实） | 无公开资料 | 无公开资料 | 无公开资料 | **基本一致**（官方营销语言与技术文档存在内部矛盾，非代码过期） | [Agnes-Ai Docs Overview](https://agnes-ai.com/en/docs/overview)、[agnes-video-v12](http://agnes-ai.com/en/docs/agnes-video-v12) |

---

## 11. 关键发现小结

1. **代码声明疑似过期/明显滞后的供应商**：**openai-sora**（官方音频是 Sora 2 发布时的核心卖点，代码完全未声明）、**grok**（官方文档与第三方评测一致指向原生音频能力，代码未声明）。
2. **能力空白（非过期，是从未建模）**：**kling** 的 Voice Control / Custom Voice 音色 ID 输入、**dashscope/Wan2.7** 的 `reference_voice` 音色一致性输入——这两家都有官方文档明确的"音色跨调用一致性"机制，但本仓库当前的 `generate_audio` 单一布尔位无法表达这一独立能力维度。
3. **官方文档内部口径不一致，需注意**：kling.ai 英文原版文档与阿里云中文代理文档对 v3/v3-omni 音频内容（是否含对话）表述不完全一致；Agnes 官方营销页与技术 API 文档口径不一致。
4. **无法确认/证伪的开放问题**：Veo 的 AI Studio vs Vertex AI 音频能力位区分、Agnes 是否为转售性质供应商——均无充分公开一手资料，已如实标注"无公开资料"或"证据不足"。
