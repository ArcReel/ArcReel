> 来源票：https://github.com/ArcReel/ArcReel/issues/1381

# 后期配音替换链路可行性调研

> 调研日期：2026-07-27。所有单价/条款/接口细节均为该日 fetch，供应商定价与条款变动频繁，工程决策前须复核 live 文档。不确定项一律标注「无公开资料」，不编造数字（遵循「不猜外部供应商数据」原则）。

## 0. 问题范围

场景：静音模型（不支持带音频参考生成）直出的视频里，角色在说话但没有声音轨。方案是后期用 TTS/voice-clone 按角色固定音色生成语音音轨，叠加回视频——是否需要配套 lip-sync（口型再对齐），以及不做 lip-sync 直接换音轨的观感代价有多大。

## 1. 结论摘要

1. **voice-clone TTS 供应商层面可行，且与本仓库已接入供应商（DashScope）高度同源**：阿里云百炼 Qwen3-TTS-VC（生产 API）10-20 秒样本即可复刻，价格与普通合成一致（0.8 元/万字符，无额外复刻费，音色创建 0.01 元/个），复用本仓库已有的 DashScope 单字段 sk- 凭证。MiniMax、火山引擎豆包也有对应能力，但均需新增多字段凭证（与既有 `lib/audio_backends/` 单一 `api_key`+`base_url` 模式不兼容，需走自定义 preset）。OpenAI 官方 Voice Engine 声音克隆技术**至今（2026-07）仍处于受限预览，未对开发者公开**，不可用。
2. **DashScope 声音复刻存在两套完全不同的合规边界，工程落地前必须分清**：控制台「体验服务」明确禁止商用（仅限体验、禁止第三方声音、禁止对外分发）；而生产级 `qwen3-tts-vc` / CosyVoice 声音复刻 API（`POST /api/v1/services/audio/tts/customization`）官方文档页未见相同的商用禁令，但也未见到该 API 层级的完整授权条款原文——这一空白点在写入正式实现前需要人工向阿里云确认，不能默认「未提及=允许」。
3. **lip-sync 工具链存在成熟的商业 API（sync.so、HeyGen）与可用的开源方案（Wav2Lip 及其衍生）**，但都是独立于视频生成模型的后处理步骤，需要新增部署/接入面；商业 API 按输出秒数计费（sync.so lipsync-2 约 $0.04/秒），开源方案需自建 GPU 推理环境。
4. **不做 lip-sync、仅替换音轨有明确的观感代价，且在社区/行业实践中被反复印证**：中文译制片行业本身就以「对口型」为配音的核心技术难点，口型不同步是被公开吐槽的观众体验痛点；AI 配音语境下的评测文章也明确指出「即使轻微不同步也显得虚假/机械」，并把行业实践拆分为「voice dubbing（到配音这一步为止）」与「visual dubbing（加一步口型重渲染）」两个不同产品档位，说明「只换音轨」是被行业认知为降级方案而非无损方案。
5. **衔接到本仓库现有架构的改动量可控但非零**：`lib/audio_backends/` 的 Registry + Protocol 模式天然支持新增一个 voice-clone 能力的 backend（新增 `AudioCapability.VOICE_CLONE` 或类似枚举 + 对应 backend 实现即可，接口层不需要大改）；`server/services/jianying_draft_service.py` 已经有「按片段 offset 独立铺一条音频轨」的先例（旁白音轨），可直接复用同一模式铺角色配音轨；但目前仓库内**没有任何 ffmpeg 音视频混流的通用模块**（`ffmpeg` 仅用于缩略图/可用性探测），若不经剪映草稿而是要直接产出成片，混流逻辑需要从零新增。

## 2. voice-clone TTS 供应商盘点

### 2.1 阿里云 DashScope（百炼）—— 与本仓库同源

本仓库 `lib/audio_backends/dashscope.py` 已接入 DashScope 同步 TTS（`qwen3-tts-flash`），复用单字段 `sk-` Bearer 凭证。声音复刻是同一模型家族的扩展能力，理论上凭证可直接复用。

**Qwen3-TTS-VC（声音复刻，推荐）**
- 模型：`qwen3-tts-vc-2026-01-26`
- 样本要求：推荐 10~20 秒，最长不超过 60 秒；音频须至少含 5 秒连续清晰朗读内容（无背景音）；格式 WAV(16bit)/MP3/M4A；≤10MB；采样率 ≥24kHz（部分模型 ≥16kHz）；单声道；不可含背景音乐/环境噪音/他人声/歌唱录音
- 流程：`POST /api/v1/services/audio/tts/customization`（`model: qwen-voice-clone`，`target_model` 须与后续合成 `model` 完全一致）→ 返回 voice name → 用该 voice name 调用 `qwen3-tts-vc-2026-01-26` 合成
- 计价：语音合成 0.8 元/万字符（与普通 Qwen3-TTS-Flash 同价，无额外复刻加价）；音色创建按 0.01 元/个计费，创建失败不计费；北京地域 90 天内有 1000 次免费音色创建额度
- 官方文档：https://help.aliyun.com/zh/model-studio/qwen-tts-voice-cloning ；https://help.aliyun.com/zh/model-studio/voice-cloning-user-guide
- **合规条款（关键，需分两处看）**：
  - 控制台「体验服务」（https://help.aliyun.com/zh/model-studio/bailian-service-notes）第三章第4条：「您承诺并保证您上传的是您本人声音录制的音频」「禁止使用任何第三方或未成年人的声音进行音频录制」「声音复刻完成后，仅支持您为体验目的使用」「不得以任何形式提供给第三方或在任何第三方平台进行使用、传播或分发」；复刻任务完成后原始语音会被删除，平台不存储原始语音。
  - 生产 API 文档页（`voice-cloning-user-guide`）本身**未见**上述商用禁令文字，只提到音色配额（每账号最多 1000 个自定义音色，1 年未使用自动删除）。两处条款是否为同一套约束（即体验服务的限制是否也约束生产 API 调用）**无公开资料可直接确认**，需人工向阿里云工单确认后再落地。

**CosyVoice-v3.5-Plus 声音复刻**
- model: `voice-enrollment`（固定），action `create_voice`，target_model `cosyvoice-v3.5-plus`，返回 `voice_id`
- 样本要求：`max_prompt_audio_length` 参数范围 [3.0, 30.0] 秒；支持 `enable_preprocess`（降噪/增强）
- 计价：合成 1.5 元/万字符，创建音色免费
- 限制：仅中国内地（北京地域）部署
- 官方文档：同上 `docs/dashscope-docs/语音合成-TTS模型.md` 汇总页（本仓库内一手核实快照，2026-06-02）

### 2.2 MiniMax

本仓库尚未接入 MiniMax（此前 TTS 调研 `docs/research/arcreel-tts-narration-research.md` 已否决其「单字段鉴权可走自定义供应商路径」的假设，2-1 票）。

- 源音频要求：mp3/m4a/wav，最短 10 秒、最长 5 分钟，≤20MB，1 份；可选示例音频 <8 秒用于增强质量
- 计价：文档未给出精确克隆/合成单价数字，仅提示查 `platform.minimax.io/docs/guides/pricing-paygo#audio`（本次未能进一步核实精确数字，**部分无公开资料**）；间接来源（阿里百炼 TTS 调研文档转述市场对比）提到 MiniMax `speech-2.8-hd` 声音复刻 9.9 元/次 + 语音合成 3.5 元/万字符，但该数字出自第三方汇总页非 MiniMax 官方定价页，**未逐字核实，标 UNVERIFIED**
- 合规条款：MiniMax 服务条款要求「若输入内容包含任何个人的声音，你必须拥有完整合法权利或已获得权利持有人的适当授权」，用户对上传声音样本负全部合规责任；禁止冒充、误导性使用、侵犯他人权利的用途
- 官方文档：https://platform.minimax.io/docs/guides/speech-voice-clone

### 2.3 火山引擎（字节跳动豆包语音）—— 与本仓库同源（Ark 已接入，但语音是独立服务）

本仓库 `ark_shared` 已接入火山方舟文本/图像/视频能力，但此前 TTS 调研已明确「豆包语音（Seed-TTS）是独立于 Ark 的服务，多字段鉴权（appid+access_token+resource_id），现有 Ark key 不能复用」。

- 声音复刻 2.0：格式支持 wav/mp3/ogg/m4a/aac/pcm；单文件 ≤10MB；同一音色最多支持 10 次上传；仅支持中文（默认）与英文两种语种（`model_type` 4/5 时）
- 计价：官方文档页（`docs.volcengine.com/docs/6561/1305191`）**未包含**具体金额，仅有计费商品名（如「声音复刻 ICL 1.0 字符版」），需查独立计费页，本次**未核实到精确数字**
- 合规条款：搜索结果引述火山引擎会「提前获取用户的充分授权，保证音色复刻过程的合法性以及声音使用的合规性」，并存在专门的《火山引擎声音复刻协议》（https://www.volcengine.com/docs/6561/1136414 ），但该页面为 JS 渲染页面，本次 WebFetch 未能取得完整条款原文，**具体条款细则无法逐条核实，仅能确认协议存在且强调授权合法性**
- 官方文档：https://www.volcengine.com/docs/6561/1305191 （声音复刻2.0）；https://www.volcengine.com/docs/6561/1533787 （生成式模型服务专用条款-语音技术）

### 2.4 OpenAI —— 官方声音克隆不可用

OpenAI 官方博客（Voice Engine）明确：该技术自 2026 年 3 月起仅向小范围可信合作伙伴做受限预览（limited preview），截至本次调研（2026-07）**仍未向开发者公开发布**，公开 `/v1/audio/speech` API 只提供固定预置音色，不支持自定义声音克隆。
- 官方来源：https://openai.com/index/navigating-the-challenges-and-opportunities-of-synthetic-voices/ ；https://openai.com/index/expanding-on-how-voice-engine-works-and-our-safety-research/ （两页均返回 403，无法直接 WebFetch 正文，以上结论转引自搜索引擎摘要中对该页面的直接引用文本，未做二次转译放大）

### 2.5 供应商盘点小结

| 供应商 | 与本仓库同源 | 声音克隆样本要求 | 计价 | 商用合规 |
|---|---|---|---|---|
| DashScope Qwen3-TTS-VC | ✅ 是 | 10-20秒（≤60秒） | 0.8元/万字符 + 0.01元/音色 | 生产 API 页未见商用禁令，但与体验服务的关系待人工确认 |
| DashScope CosyVoice | ✅ 是 | 3-30秒 | 1.5元/万字符，创建免费 | 无公开资料单独说明 |
| MiniMax | ❌ 否（此前已否决单字段路径假设） | 10秒-5分钟 | 无公开精确数字 | 需权利人授权，用户自证 |
| 火山豆包 | 部分（Ark 已接，语音独立） | 无公开精确时长要求 | 无公开精确数字 | 协议存在，条款细节未核实 |
| OpenAI | ✅ 是（文本/图像已接） | 不适用 | 不适用 | 未公开发布，不可用 |

## 3. lip-sync 工具链现状

### 3.1 商业 API

**sync.so（Sync Labs）**
- 输入输出：视频 + 音频轨道输入，输出对齐后视频；`lipsync-2`/`lipsync-2-pro` 输出 512×512，`sync-3` 支持 4K 原生输出；处理按 25fps 计费；时长上限随订阅档位从 Hobbyist 1 分钟到 Scale+ 30 分钟不等
- 效果口碑（官方文档自述的边界条件）：需要「自然说话动作」，静止画面口型不生效；极端侧脸角度下 `lipsync-2` 效果下降，`sync-3` 原生支持极端角度；多人场景官方建议先用外部工具遮罩/裁剪出目标人脸
- 成本：按输出秒数计费，`lipsync-2` 约 $0.04-0.05/秒，`lipsync-2-pro` 约 $0.067-0.083/秒，`sync-3` 约 $0.107-0.133/秒（4K）；另有月度订阅档 Free/Hobbyist $5/Creator $19/Growth $49/Scale $249
- 官方文档：https://sync.so/docs/models/lipsync ；定价：https://sync.so/pricing

**HeyGen**
- 视频翻译+口型对齐一体化产品：可翻译至 175+ 语言并保留说话人音色与口型；API 按 $0.05/秒计费（Avatar V/IV lip sync 输出），$5 起充，无需订阅
- 关键对比数据：视频翻译若带口型对齐（full video translation with lip sync）为 5 credits/分钟，**仅换音轨不做口型对齐（audio-only dubbing, no lip sync）为 2 credits/分钟**——HeyGen 官方产品本身把「是否做口型对齐」明确列为两档不同价位的独立功能，侧面印证行业把「只换音轨」当作降级但仍然是被认可的产品形态之一
- 官方文档：https://developers.heygen.com/ ；定价：https://help.heygen.com/en/articles/10060327-heygen-api-pricing-explained

**D-ID**
- 提供 Web 界面与开发者 API，用于「照片/头像 + 文本或音频 → 说话视频」；订阅档位从 Lite $4.70/月起（含每月 10 分钟生成额度，标准头像，带水印）
- 本次未取得 D-ID 完整官方 API 文档定价页（搜索结果为第三方汇总），**精确计费细则无公开一手资料核实**，仅确认产品存在且提供 API

### 3.2 开源方案

| 方案 | GitHub | 输入输出 | 效果口碑（第三方评测转述，非项目自述） | 部署要求 |
|---|---|---|---|---|
| **Wav2Lip** | 2020 年论文模型，社区多个 fork（Easy-Wav2Lip、Wav2Lip-HQ）维持至今 | 音频 + 视频/图像 → 修改口型区域的视频 | 第三方评测：正面、光线良好的英文说话人脸效果好；主要短板是嘴部区域视觉质量偏模糊；极端头部角度（约 45°以上）在所有测试工具上都会劣化；被称为「六年后依然是市面近一半 AI 视频产品背后默认的口型引擎」 | Python + PyTorch + CUDA GPU，需下载模型权重、手动安装依赖 |
| **SadTalker** | 单张照片 + 音频 → 带头部运动的说话视频（3DMM 全脸动画） | 第三方评测：口型精度不如 Wav2Lip 精细，尤其语速快时嘴部细节容易丢失 | 无公开硬件要求资料 |
| **MuseTalk** | 视频/图像 + 音频，潜空间扩散模型 | 论文自述在视觉保真度与口型同步准确度上优于同期方法；第三方定性评价「牙齿等细节比 GAN 方案更准确」；论文报告一项对比评测中 MuseTalk 视觉质量评分 4.26、口型同步评分 3.77（满分制未注明，出自比较文章转述） | 论文称支持 NVIDIA Tesla V100 上 30fps+ 实时推理 |
| **LatentSync**（字节跳动） | 基于 Stable Diffusion 的音频条件潜扩散口型对齐，无中间运动表征 | 同一对比评测中口型同步评分 4.07、视觉质量评分 3.71（高于 MuseTalk 口型分、低于其视觉分）；第三方评价身份一致性保持较强，但像素细节可能略逊像素空间方案 | 无公开硬件要求资料 |
| **VideoReTalking** | 完整视频 + 新音频 → 仅修改口型的视频（后期制作导向） | 无公开量化评测，仅有功能定位描述 | 无公开硬件要求资料 |

GitHub 仓库：Wav2Lip 社区多个衍生仓库活跃维护；LatentSync 官方仓库 https://github.com/bytedance/LatentSync 。MuseTalk 论文 https://arxiv.org/pdf/2410.10122 。上述对比评测数据出处：https://lipsync.com/blog/open-source-lip-sync （第三方评测站，非项目官方自述，本报告按「社区证据」标注而非权威结论）。

## 4. 不做 lip-sync、仅替换音轨的口型错位代价评估

**中文配音行业实践**：口型对齐是译制片配音公认的核心技术难点——「口型与台词的精准对应」要求配音演员在严格时间限制内匹配原片演员的口型开合、停顿、节奏；为了对上口型甚至需要调整语序、增减字数、改变语速。现代译制片配音质量下降是被公开讨论的观众吐槽点，直接影响观众是否愿意看译制版还是转看原版。来源：知乎「为什么我国译制片的配音都带着略显夸张做作的腔调？」https://www.zhihu.com/question/19792456 ；相关技术文章（火山引擎开发者社区）https://developer.volcengine.com/articles/7644544027820572681 。

**AI 配音产品评测**：行业评测文章明确把 AI 配音拆成两个产品档位——「voice dubbing」（语音克隆到位但不改口型，流程止于 TTS 输出）与「visual dubbing」（额外加一步口型重渲染）；并指出「哪怕只是几帧的口型不同步也会显得虚假/机械」「不完整的语句口型移动看起来就是不对」，不完美的口型同步会造成「视觉不协调，降低参与度、信任度和感知专业度」。来源：https://dubly.ai/blog/perfect-lip-sync-why-it-matters-and-why-most-tools-fail 。

**HeyGen 产品定价侧证**（见 3.1）：官方把「口型对齐」与「仅换音轨」列为两档独立计费产品（5 credits/分钟 vs 2 credits/分钟），说明行业内「仅换音轨」是被承认的可用降级方案，价格更低，但官方仍单独把「加口型对齐」作为可选的溢价升级项，暗示其被视为观感提升手段而非可有可无的选项。

**结论**：口型错位的代价在近景/正脸镜头、需要建立观众信任感的内容（如角色特写台词）中最明显；在远景、快切、非正脸角度的镜头中影响相对较小。本仓库场景是小说转短视频，镜头构图和角色特写比例目前**无公开资料/未在本次调研中核实**，无法给出量化的"多大比例镜头会踩雷"的结论——这需要结合 ArcReel 实际分镜数据另行评估。

## 5. 与本仓库现有架构的衔接点评估

### 5.1 `lib/audio_backends/`（`base.py` / `dashscope.py` / `openai.py` / `registry.py`）

- 当前架构：`AudioBackend` 是一个 `Protocol`（`lib/audio_backends/base.py:40`），只声明 `name` / `model` / `capabilities` / `synthesize()`；`AudioCapability` 目前只有一个成员 `TEXT_TO_SPEECH`（`base.py:11-14`）；`registry.py` 是纯字典工厂（`register_backend` / `create_backend`），新增一个 backend 不需要改接口层代码。
- 接入 voice-clone 的可行路径：新增一个能力枚举（如 `AudioCapability.VOICE_CLONE` 或类似）+ 一个新的 backend 类（例如 `DashScopeVoiceCloneBackend`），复用现有 `DashScopeAudioBackend` 里已经写好的 submit/retry/download 骨架（`dashscope.py:92-155`，两段式：合成请求单独重试、下载单独重试，避免重复计费）。**声音克隆本身分两步（先注册音色、再合成），与现有 `synthesize()` 单步协议不完全对齐**，需要额外一个"注册/克隆音色"的方法或在 backend 初始化时接受一个已知的 voice id（更符合当前"角色固定音色"的产品设想——先离线注册好每个角色的音色，运行期只调用合成）。
- OpenAI backend（`openai.py`）当前主要服务自定义供应商 OpenAI 兼容 audio 通路（Fish Audio、自托管 shim 等，见文件头注释），本身不具备声音克隆能力（`/v1/audio/speech` 只接受预置 `voice` 字符串），OpenAI 官方声音克隆不可用（见 2.4），故该 backend 无法承载 voice-clone 需求。

### 5.2 `server/services/jianying_draft_service.py`

- 已有先例：`_generate_draft()` 方法里，视频轨（`TrackType.video`，line 297）与旁白音频轨（`TrackType.audio`，"旁白"，line 419）是两条独立的轨道；旁白音频按每个片段的 `offset_us` 精确定位（line 388-421），时长取音频文件真实时长而非视频时长，超长时收口到下一段起点避免重叠（line 406-421）。
- 因为本方案的前提是「静音模型直出」，源视频片段（`VideoSegment`，line 341）本身不携带任何语音音轨（代码里也确认没有任何 `volume`/`mute` 相关调用，说明现状是直接透传素材原始音轨——静音场景下这条不构成冲突），所以给角色台词铺一条独立音频轨（角色配音轨，命名类比"旁白"）在技术上是对现有"旁白音轨"模式的直接复用，改动量小：新增一个类似 `narration_placements` 的角色台词音频列表，按同样的 offset 铺轨即可，**不需要处理"消除原音轨"的复杂度**（因为原本就没有）。
- 若要做 lip-sync，则口型重渲染必须发生在剪映草稿生成**之前**（对 `local_path` 指向的视频素材做替换），剪映草稿导出阶段只负责编排已经处理好的素材，不承担口型渲染职责——这意味着 lip-sync 步骤要么插入生成流水线（视频生成后、草稿导出前），要么作为一个独立的后处理任务打上新的素材版本。

### 5.3 ffmpeg 混流

- 全仓库搜索确认，`ffmpeg` 目前仅用于 `lib/thumbnail.py`（生成缩略图）与 `server/app.py`/`server/agent_runtime/agent_access_policy.py`（可用性探测/沙箱白名单），**没有任何音视频混流（mux）的通用模块**。
- 若产品方向是绕开剪映草稿、直接产出成片（而非依赖用户在剪映里手动导出），则需要新增一个 ffmpeg 音视频混流封装（读取静音视频 + 生成的语音轨 → 输出合成视频），这是一块全新代码，非改造现有代码。剪映草稿路径（5.2）不需要这块，因为草稿导出让剪映软件自己做最终渲染混流。

## 6. 参考资料清单

### voice-clone TTS
- 阿里云 Qwen3-TTS 声音复刻 API：https://help.aliyun.com/zh/model-studio/qwen-tts-voice-cloning
- 阿里云声音复刻用户指南：https://help.aliyun.com/zh/model-studio/voice-cloning-user-guide
- 阿里云百炼服务特别说明（体验服务合规条款）：https://help.aliyun.com/zh/model-studio/bailian-service-notes
- 本仓库既有一手核实快照：`docs/dashscope-docs/语音合成-TTS模型.md`、`docs/research/arcreel-tts-narration-research.md`
- MiniMax Voice Clone 官方文档：https://platform.minimax.io/docs/guides/speech-voice-clone
- 火山引擎声音复刻2.0：https://www.volcengine.com/docs/6561/1305191 （= https://docs.volcengine.com/docs/6561/1305191）
- 火山引擎声音复刻协议：https://www.volcengine.com/docs/6561/1136414
- 火山引擎生成式模型服务专用条款-语音技术：https://www.volcengine.com/docs/6561/1533787
- OpenAI Voice Engine 说明：https://openai.com/index/navigating-the-challenges-and-opportunities-of-synthetic-voices/ ；https://openai.com/index/expanding-on-how-voice-engine-works-and-our-safety-research/

### lip-sync
- sync.so 模型文档：https://sync.so/docs/models/lipsync ；定价：https://sync.so/pricing
- HeyGen 开发者文档：https://developers.heygen.com/ ；API 定价：https://help.heygen.com/en/articles/10060327-heygen-api-pricing-explained
- LatentSync（字节跳动）：https://github.com/bytedance/LatentSync
- MuseTalk 论文：https://arxiv.org/pdf/2410.10122
- 开源方案第三方评测汇总：https://lipsync.com/blog/open-source-lip-sync
- 口型对齐观感代价评测文章：https://dubly.ai/blog/perfect-lip-sync-why-it-matters-and-why-most-tools-fail
- 中文译制片配音口型讨论：https://www.zhihu.com/question/19792456 ；https://developer.volcengine.com/articles/7644544027820572681

### 本仓库代码依据
- `lib/audio_backends/base.py`（`AudioBackend` Protocol、`AudioCapability` 枚举）
- `lib/audio_backends/dashscope.py`（DashScope TTS 同步适配器，两段式重试）
- `lib/audio_backends/openai.py`（OpenAI 兼容 TTS，自定义供应商通路）
- `lib/audio_backends/registry.py`（Registry + Factory）
- `server/services/jianying_draft_service.py`（视频轨/字幕轨/旁白音轨编排，line 281-450 附近）
