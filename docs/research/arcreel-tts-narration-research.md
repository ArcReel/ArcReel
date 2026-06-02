# ArcReel TTS（旁白配音）选型与接入调研报告

> 调研日期：2026-06-02。所有单价/模型名/接口细节均为该日 fetch，TTS 定价变动频繁，工程决策前须复核 live 文档。
> 不确定项一律标注 **UNVERIFIED**，不编造（遵循"不猜外部供应商数据"原则）。
> 本报告对应的本期接入决策见 `docs/adr/0010-tts-audio-through-generation-queue.md` 与 `CONTEXT.md`「媒体类型与配音（TTS）」。

## 0. 调研范围与定位

为 ArcReel（中文小说→短视频）选型 TTS，**本期用于说书（narration）模式的旁白配音**。

- **评选第一标准**：中文旁白质量（朗读式长文本的语气/停顿/情感/连贯性）。架构不绑定语言，逐家标注语种覆盖，最终由用户自选。
- **两条接入路径都覆盖**：A. 云 API 供应商（主接入路径）；B. 自托管/端侧开源模型（隐私/成本对比）。
- **ArcReel 接入约束**：① 自定义供应商凭证固定为单字段 `api_key` + `base_url`（`docs/adr/0008`），多字段鉴权只能内置 preset；② OpenAI 兼容 `/v1/audio/speech` 可复用现有 OpenAI 客户端工厂、走中转；③ 声明式定价按 `kind` 声明（`docs/adr/0009`）；④ 同步 vs 异步决定 backend 仿 `text_backends` 还是 `video_backends`；⑤ 已接入供应商（Gemini / 火山 Ark / OpenAI / Grok / Vidu / 阿里百炼 DashScope）若 TTS 能复用凭证则接入成本最低。

调研方法：两轮对抗式 deep-research（109 + 9 agent）+ 专项补洞（时长错位 / 已接入供应商规格 / 水印）。结论标注置信度与投票（如 3-0 = 三票确认，2-1 = 多数确认，0-3/1-2 = 被否决）。

## 1. 结论先行

### 1.1 分层推荐

| 档位 | 首选 | 理由 | 代价 |
|---|---|---|---|
| **最低接入成本** | **阿里 DashScope Qwen-TTS** | 复用 ArcReel 已有 DashScope 单字段 sk- 凭证（配置零新增表单）；低摩擦档里中文最佳；Qwen-TTS 同步 HTTP 契合同步 backend | TTS 走原生 `/api/v1` 路径（非 OpenAI 兼容），需写同步适配器；单价 UNVERIFIED |
| **可扩展/自助** | **自定义供应商 OpenAI 兼容 audio 通路** | 单字段 + 真 OpenAI 兼容；用户自助接 Fish / 自托管 IndexTTS（套 shim）/ 中转，ArcReel 不必逐家写码 | 需新增 audio `EndpointSpec` + `CustomAudioBackend` + `infer_endpoint` audio 模式 |
| **最佳中文质量** | **火山豆包 大模型语音合成** / ElevenLabs | 豆包：325 音色、原生中文方言、声音复刻 2.0、情感连贯；ElevenLabs：Multilingual v2/v3 中文口碑优 | 豆包多字段鉴权（appid+access_token+resource_id）须内置 preset，**Ark key 不能复用**（Seed Speech 是独立服务）；均非 OpenAI 兼容 |
| **自托管隐私** | **CosyVoice2** / **VoxCPM-0.5B** | 均 Apache 2.0 可商用；VoxCPM 中文 CER 0.93% 开源最佳 | 需 GPU；无官方 OpenAI 兼容封装；显存/RTF UNVERIFIED |

### 1.2 本期决策（v1）

**DashScope Qwen-TTS preset（复用凭证、按字符计费、同步原生适配器）+ 自定义供应商 OpenAI 兼容 audio 通路（`openai-tts` → `/v1/audio/speech` + `CustomAudioBackend`）。** Fish / 自托管 IndexTTS（套 shim）/ 中转走自定义通路。详见 `docs/adr/0010`。

## 2. 云 API 供应商对比

### 2.1 主对比表

| 供应商 | 中文 | OpenAI 兼容 `/v1/audio/speech` | 同步/异步 | 鉴权 | 计费维度 | ArcReel 已接入 | 置信 |
|---|---|---|---|---|---|---|---|
| **OpenAI** tts-1 / tts-1-hd / gpt-4o-mini-tts | 英文优、中文弱 | ✅ 是（业界标准形态） | 同步直返字节 + chunked 流式 | 单字段 | 按字符（tts-1 $15、tts-1-hd $30 /1M chars） | 文本/图像已接（TTS 未接） | 3-0；单价 2-1 |
| **ElevenLabs** Multilingual v2/v3, Flash/Turbo | 优 | ❌ 私有 | 同步（`/stream` chunked，仍是一次性请求） | 单字段 | 按字符（Flash/Turbo $0.05、Multilingual $0.10 /1K chars） | 否 | 3-0 |
| **阿里 DashScope** Qwen-TTS / CosyVoice | 强 | ❌ TTS **不在**兼容路径（兼容路径仅 chat） | Qwen-TTS 同步 HTTP；CosyVoice realtime = WebSocket | 单字段 Bearer（sk-） | 按字符 / 万字符（CosyVoice ~¥2/万字符 **UNVERIFIED**） | ✅ 凭证可复用 | 3-0；单价 UNVERIFIED |
| **火山豆包** 大模型语音合成（Seed-TTS） | 强（原生方言） | ❌ 私有（openspeech.bytedance.com） | 同步 HTTP + WSS 流式 + **异步长文本**（submit→query ≤10万字，音频存 7 天） | **多字段**（appid+access_token+resource_id） | 按字符 / 字数包 + 音色按年 | ❌ Ark key 不复用 | 3-0 |
| **MiniMax** T2A async | 强 | ❌ 私有（`t2a_async_v2`） | **异步**（建任务→轮询→取 file_id，URL 9h 过期） | 单字段 **被否决**（1-2，勿假定可走自定义） | credit 订阅档 **被否决**（0-3） | 否 | 接口 3-0 |
| **Fish Audio**（OpenAudio S1） | 良 | ✅ 是（原生 `/v1/audio/speech`） | 同步流式（TTFB<150ms） | 单字段 Bearer | 按 **UTF-8 字节**（$15/1M bytes） | 否（最佳自定义通路候选） | 补洞单轮 |
| **Gemini** 原生音频 TTS | 良（cmn） | ❌ `generateContent` 内联 base64、**无流式** | 同步 | 单字段（x-goog-api-key） | 按 **token** | ✅ 凭证可复用 | 补洞单轮 |
| **Google Cloud TTS**（Chirp3/Neural2/WaveNet） | 良（cmn-CN） | ❌ `synthesize` REST | 同步；长音频异步 LRO | **多字段**（GCP service-account） | 按字符（$4–$160/1M） | 凭证族同 Vertex（需开通 API） | 补洞单轮 |
| **Azure AI Speech** | 良（zh-CN+方言） | ❌ | 同步实时 + 异步 batch | **多字段**（subscription key + region） | 按字符（$16/1M std） | 否 | 补洞单轮 |
| **腾讯云 TTS** | 强 | ❌ 签名 API | 同步 + 流式 | **多字段**（AppID+SecretId+SecretKey 签名） | 按字符分档 | 否 | 补洞单轮 |
| **科大讯飞 TTS** | 顶级 | ❌ WSS | 流式 WebSocket | **多字段**（APPID+APIKey+APISecret HMAC 签名） | 按字符 / 套餐 | 否 | 补洞单轮 |

### 2.2 关键边界

- **OpenAI 计费口径分裂**：主定价页已转向 realtime 按 token，专用按字符 TTS 模型（tts-1 $15、tts-1-hd $30 /1M chars）**不在主表**，须查 `developers.openai.com/api/docs/models/tts-1`。"定价已转向 token"结论被验证者判为略夸大（2-1）。
- **DashScope 凭证可复用但 TTS 非兼容路径**：单字段 Bearer 可复用，但 OpenAI 兼容接口只暴露 chat completions，TTS 走原生 `/api/v1/services/aigc/multimodal-generation/generation` —— 不能复用 OpenAI 客户端工厂/中转，须写原生适配器。
- **豆包"凭证陷阱"**：豆包语音（Seed-TTS）是独立于 Ark 的服务，多字段鉴权，**ArcReel 现有 Ark key 不能复用**，须内置 preset + 新建多字段凭证表单。
- **MiniMax 两条 claim 被否决**：单字段鉴权（1-2）与 credit 订阅档（0-3）均未通过验证——**不可据此断言 MiniMax 可走自定义供应商路径**。
- **声明式定价 kind**：跨厂分裂为 **按字符**（OpenAI/ElevenLabs/DashScope/GCloud/Azure/腾讯/讯飞）、**按 token**（Gemini）、**按 UTF-8 字节**（Fish）三类。`docs/adr/0009` 声明式定价本期至少需 `per_character` kind。

## 3. 自托管/端侧开源 TTS（隐私档，含授权陷阱）

| 模型 | 中文质量 | 许可证 / 商用 | 备注 | 置信 |
|---|---|---|---|---|
| **CosyVoice2-0.5B** | 强（CER 1.38%） | ✅ Apache 2.0，代码+权重均可商用、无版税 | FunAudioLLM；声音复刻 | 3-0 |
| **VoxCPM-0.5B** | **开源最佳（CER 0.93%）** | ✅ Apache 2.0 可商用 | tokenizer-free，1.8M 小时中英；超 IndexTTS2/CosyVoice2 | 3-0（arXiv 预印本作者自报，未同行评审） |
| **IndexTTS2** | 强（CER 1.03%，开源第二） | ⚠️ 权重商用须 bilibili 授权（v1 须书面授权；**v2 阈值制**：>1亿 MAU 或 >10亿 RMB 年营收才需单独许可，中小商用大概率免） | bilibili；情感控制强；论文有**时长控制**但 GitHub 标注"本版本尚未启用"；**无官方 API server / 无 OpenAI 兼容端点**，接 ArcReel 须自套 shim；GPU 显存/RTF UNVERIFIED | 许可 2-1 |
| **F5-TTS** | 良 | ⚠️ base 权重 **CC-BY-NC 禁商用**（Emilia 数据集，finetune 后仍禁；代码 MIT）；须自有商用数据从头训练 | 商用陷阱 | 3-0 |

**结论**：自托管隐私档首选 **CosyVoice2 / VoxCPM**（均 Apache 2.0 干净可商用）。IndexTTS / F5-TTS 有商用授权陷阱，选用前必须逐版本核对许可。所有自托管模型接 ArcReel 的干净方式 = 自托管时在前面套 OpenAI 兼容 `/v1/audio/speech` 服务，走自定义供应商 audio 通路插入。

## 4. 关键风险与未知点

### 4.1 时长错位（最关键；本期暂缓但记录解法）

TTS 真实语音时长 ≠ 旁白脚本 `NarrationSegment.duration_seconds`（该预设时长同时驱动视频片段长度与字幕时间轴）。**业界标准解法：反转契约——音频驱动时间轴，`duration_seconds` 降级为生成提示。**

- ArcReel 既有 `server/services/jianying_draft_service.py` **本就按真实素材时长排版**（视频与字幕 `trange` 都用 `actual_duration_us = material.duration`），"最长真实素材赢"已是现有设计。
- 音频比视频长 → **拉伸视频**（`setpts`/`tpad` 冻帧，无质量损失），**绝不加速旁白**。
- 字幕**从真实音频生成**：优先供应商字符级时间戳（ElevenLabs `with-timestamps`，中文可用、零额外基建）；兜底 WhisperX 强制对齐（中文仅社区模型 `jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn`，精度偏弱）。
- ⚠️ **别指望"等时 TTS"**（target_duration 参数）：商用中文云 API 无一暴露，仅研究级（Amazon ICASSP'22、VideoDubber AAAI'23 明说中文更难因 token 数≠语音时长）。自托管 IndexTTS2 论文有时长控制但当前版本未启用。
- ⚠️ 语速参数控时不可靠：各家区间不一（ElevenLabs 0.7–1.2、OpenAI 0.25–4.0、豆包 0.8–2.0、MiniMax 0.5–2.0），且 **OpenAI `gpt-4o-mini-tts` 历史上忽略 speed**（官方称文档 bug）；只可作 ±10% 次级微调。`ffmpeg atempo` 仅 0.5–2.0x 干净，语音超 ~10–15% 拉伸即可闻劣化。

> 本期决策：**简单附带剪映音轨（每段独立、不对齐，用户手动精调）**，不做自动重定时/字幕重排（暂缓）。上述解法供后续启用时参考。

### 4.2 其他

- **长文本**：MiniMax 内联 text 上限 50K 字符、文件路径达 1M（2-1）；豆包异步长文本 ≤10万字。按 segment 旁白短，本期无须分块。
- **音色一致性**：固定单一音色即可跨段一致；声音复刻（豆包 2.0 / CosyVoice）可进一步锁音色（本期用预置音色，克隆留后续）。
- **流式 vs 整文件**：ArcReel 需落地可版本化音频文件 + 喂剪映音轨，**整文件接收即可，流式反增复杂度**（同步 backend 一次性取字节）。
- **水印 / 合规**：所选供应商**均不对预置音色强制不可关水印**（MiniMax `aigc_watermark` 默认 false）。ArcReel 为开源项目，AI 标识合规义务不在本项目（用户决策，本报告不展开）。

## 5. 本期接入决策与实现面（摘要）

完整决策记录见 `docs/adr/0010` 与 `CONTEXT.md`。要点：

- **媒体类型**：新增第 4 个 `media_type` = `audio`（capability = `text_to_speech`），与 image/video/text 平级。
- **调度**：走 GenerationQueue/Worker（audio lane），像 image/video；**backend 同步**（仿 `text_backends`，秒回，无 submit-poll-resume）。`enqueue_tts(segment_ids?)`。
- **后端**：新增 `lib/audio_backends/`；DashScope 同步原生适配器 + 自定义 OpenAI 兼容 audio 通路。
- **音色**：可配置字符串 id（全局默认 + `project.json` `settings.narration_voice` 覆盖，不内置目录，可选语速）。
- **版本化**：是（resource_paths/VersionManager，目录 `audio/`，文件 `segment_{id}.mp3`）。
- **数据模型**：`GeneratedAssets.narration_audio: str | None`（project.json，无 DB 表）；文本源 = `NarrationSegment.novel_text` 原样。
- **计费**：`per_character` pricing kind（DashScope preset）；自定义供应商用 DB 内用户填单价。

## 6. 实现前必须核实（UNVERIFIED 清单）

外部供应商数据，**不猜不硬编码**，编码前逐项查一手文档：

1. DashScope TTS **确切模型 id**（qwen-tts / qwen3-tts-flash / cosyvoice-v2?）与哪个是**纯同步 HTTP**（CosyVoice realtime 是 WebSocket，不契合同步 backend → 倾向 Qwen-TTS）。
2. DashScope TTS **同步 REST 端点 + 请求/响应 schema**、**按字符单价 + 币种**、**可用音色 id**、输出格式/采样率。
3. OpenAI 兼容 `/v1/audio/speech` 请求 schema（model/voice/input/response_format/speed），用于自定义 audio `EndpointSpec`。
4. 自定义供应商 `price_unit` 是否支持按字符单位（audio）。
5. Fish Audio 是否对 OpenAudio S1 云端强制水印（UNVERIFIED）。
6. 自托管模型显存/RTF/是否有官方 OpenAI 兼容封装（CosyVoice2 / VoxCPM / IndexTTS2）。

## 7. 参考资料（一手官方来源，2026-06-02 fetch）

- OpenAI TTS 指南：https://developers.openai.com/api/docs/guides/text-to-speech
- OpenAI tts-1 模型/定价：https://developers.openai.com/api/docs/models/tts-1
- ElevenLabs 定价：https://elevenlabs.io/pricing/api ；流式：https://elevenlabs.io/docs/api-reference/text-to-speech/stream ；字符级时间戳：https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps
- 火山豆包 大模型语音合成：https://www.volcengine.com/docs/6561/1257543 ；异步长文本：https://www.volcengine.com/docs/6561/1829010 ；声音复刻 2.0：https://www.volcengine.com/docs/6561/1305191
- 阿里 DashScope OpenAI 兼容：https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope ；Qwen-TTS：https://www.alibabacloud.com/help/en/model-studio/qwen-tts ；定价：https://help.aliyun.com/zh/model-studio/model-pricing
- MiniMax T2A async：https://platform.minimax.io/docs/guides/speech-t2a-async
- Fish Audio 定价/限流：https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits
- Gemini 语音生成：https://ai.google.dev/gemini-api/docs/speech-generation
- Google Cloud TTS 定价：https://cloud.google.com/text-to-speech/pricing
- Azure Speech 定价：https://azure.microsoft.com/en-us/pricing/details/cognitive-services/speech-services/
- 腾讯云 TTS：https://cloud.tencent.com/document/product/1073/37995 ；讯飞 TTS：https://www.xfyun.cn/doc/tts/online_tts/API.html
- CosyVoice LICENSE：https://github.com/FunAudioLLM/CosyVoice/blob/main/LICENSE
- VoxCPM 论文：https://arxiv.org/html/2509.24650v1 ；权重：https://huggingface.co/openbmb/VoxCPM-0.5B
- IndexTTS：https://github.com/index-tts/index-tts ；许可：https://huggingface.co/spaces/IndexTeam/IndexTTS-2-Demo/blob/main/INDEX_MODEL_LICENSE_EN.txt ；许可讨论：https://github.com/index-tts/index-tts/issues/228
- F5-TTS 商用限制：https://github.com/SWivid/F5-TTS/discussions/997
- 时长对齐：WhisperX https://github.com/m-bain/whisperX ；自动配音时长建模 https://arxiv.org/html/2211.16934v2 ；语音翻译到自动配音 https://aclanthology.org/2020.iwslt-1.31/ ；ffmpeg atempo https://ayosec.github.io/ffmpeg-filters-docs/7.1/Filters/Audio/atempo.html
- 自托管套 OpenAI 兼容 shim 范式：https://github.com/matatonic/openedai-speech
