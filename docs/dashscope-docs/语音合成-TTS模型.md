# 阿里百炼语音合成（TTS）模型调研

更新时间：2026-06-02（基于百炼控制台模型市场 + 官方文档核实）

本文档为 ArcReel issue #707 集成 DashScope TTS 供应商的调研参考。

## 模型总览

百炼模型广场 TTS 分类共 16 个模型，经筛选后值得收录的分为三个系列：Qwen3-TTS（推荐）、CosyVoice、Qwen-TTS（legacy）。MiniMax-Speech 为三方直供、价格偏高（3.5 元/万字符 + 声音复刻 9.9 元/次），Sambert 为旧模型（官方建议迁移），均不推荐新项目使用。

### 定价对比

货币：**人民币（CNY）**，按字符计费（除 qwen-tts 按 token 计费）。

| 模型系列 | model code | 语音合成单价 | 声音复刻/设计 | RPM | 备注 |
|---------|-----------|------------|------------|-----|------|
| Qwen3-TTS-Flash | `qwen3-tts-flash` | 0.8 元/万字符 | — | 180 | **推荐**，性价比最高 |
| Qwen3-TTS-Instruct-Flash | `qwen3-tts-instruct-flash` | 0.8 元/万字符 | — | 180 | 支持自然语言指令控制语气/情感 |
| Qwen3-TTS-VC（声音复刻） | `qwen3-tts-vc-2026-01-26` | 0.8 元/万字符 | — | 180 | 10-20 秒音频即可复刻 |
| Qwen3-TTS-VD（声音设计） | `qwen3-tts-vd-2026-01-26` | 0.8 元/万字符 | 0.2 元/次 | 180 | 文本描述生成音色 |
| Qwen3-TTS-Flash-Realtime | `qwen3-tts-flash-realtime` | 0.8 元/万字符 | — | 180 | WebSocket 流式 |
| Qwen3-TTS-Instruct-Flash-Realtime | `qwen3-tts-instruct-flash-realtime` | 1 元/万字符 | — | 180 | 实时+指令控制 |
| Qwen3-TTS-VC-Realtime | `qwen3-tts-vc-realtime-2026-01-15` | 0.8 元/万字符 | — | 180 | 实时声音复刻 |
| Qwen3-TTS-VD-Realtime | `qwen3-tts-vd-realtime-2026-01-15` | 0.8 元/万字符 | 0.2 元/次 | 180 | 实时声音设计 |
| CosyVoice-v3.5-Plus | `cosyvoice-v3.5-plus` | 1.5 元/万字符 | — | 180 | 超高表现力，多语种最佳 |
| CosyVoice-v3.5-Flash | `cosyvoice-v3.5-flash` | 0.8 元/万字符 | — | 3 RPS | 低延迟版 |
| Qwen-TTS（legacy） | `qwen-tts` | 输入 1.6 + 输出 10 元/百万token | — | 10 | 仅中国内地，按 token 计费 |

> Qwen-TTS legacy 音频 token 换算：每 1 秒音频 = 50 tokens，不足 1 秒按 50 tokens 计。

---

## 一、Qwen3-TTS 系列（推荐）

### 1. Qwen3-TTS-Flash — 标准语音合成

最新一代千问 TTS，17 种高表现力音色，支持中文方言和多语种。

**model codes**:
- `qwen3-tts-flash`（稳定版，= `qwen3-tts-flash-2025-11-27`）
- `qwen3-tts-flash-2025-11-27`（快照）
- `qwen3-tts-flash-2025-09-18`（旧快照，RPM 仅 10）

**支持语种**: 中文（普通话、上海话、北京话、四川话、南京话、陕西话、闽南语、天津话）、粤语、英文、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语

**输出格式**: wav（非流式）/ pcm Base64（流式），采样率 24kHz

**API 调用（非实时，Python）**:

```python
import dashscope

# 非流式 — 返回音频文件 URL（有效期 24 小时）
response = dashscope.MultiModalConversation.call(
    model="qwen3-tts-flash",
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    text="要合成的文本",
    voice="Cherry",
    language_type="Chinese",
    stream=False,
)
audio_url = response.output.audio.url

# 流式 — 逐段返回 Base64 PCM
for chunk in dashscope.MultiModalConversation.call(
    model="qwen3-tts-flash",
    text="要合成的文本",
    voice="Cherry",
    language_type="Chinese",
    stream=True,
):
    pcm_b64 = chunk.output.audio.data  # Base64 encoded PCM, 24kHz
```

**HTTP API**:

```http
POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
Authorization: Bearer $DASHSCOPE_API_KEY
Content-Type: application/json

{
  "model": "qwen3-tts-flash",
  "input": {
    "text": "要合成的文本",
    "voice": "Cherry",
    "language_type": "Chinese"
  }
}
```

流式请求额外加 header: `X-DashScope-SSE: enable`

国际（新加坡）endpoint: `https://dashscope-intl.aliyuncs.com/api/v1/...`

### 2. Qwen3-TTS-Instruct-Flash — 指令控制语音合成

在 Flash 基础上增加自然语言指令控制，可精确控制语气、语速、情感、角色。支持 25 个音色的中英文 Instruct 调节。

**model codes**:
- `qwen3-tts-instruct-flash`（稳定版，= `qwen3-tts-instruct-flash-2026-01-26`）
- `qwen3-tts-instruct-flash-2026-01-26`（快照）

**独有参数**:
- `instructions`：自然语言描述，控制语气/语速/情感/角色，最大 1600 tokens，仅支持中英文
- `optimize_instructions`：布尔值，开启后自动优化 instructions

**示例 instructions**: `"语速较快，带有明显的上扬语调，适合介绍时尚产品"` / `"用低沉沙哑的声音，缓慢地讲述一个悲伤的故事"`

**ArcReel 适用场景**: drama 模式下按角色情感调整语气，narration 模式下控制朗读风格

### 3. Qwen3-TTS-VC — 声音复刻

10-20 秒音频即可复刻声音，无需训练。

**流程**:

```text
1. 注册音色 → POST /api/v1/services/audio/tts/customization
   model: "qwen-voice-clone"
   target_model: "qwen3-tts-vc-2026-01-26"  (必须与合成时 model 一致)
   audio: base64 音频数据
   → 返回 voice name

2. 使用复刻音色合成 →
   model: "qwen3-tts-vc-2026-01-26"
   voice: "<上一步返回的 voice name>"
```

**关键约束**: `target_model` 必须与后续语音合成时的 `model` 完全一致，否则合成失败。

### 4. Qwen3-TTS-VD — 声音设计

通过文本描述生成定制化音色，无需音频样本。

**流程**:

```text
POST /api/v1/services/audio/tts/customization
model: "qwen-voice-design"
target_model: "qwen3-tts-vd-2026-01-26"
action: "create"
voice_prompt: "一个温柔的年轻女性声音，略带沙哑，说话节奏缓慢"  (最大 2048 字符)
preview_text: "今天天气真好"  (最大 1024 字符)
preferred_name: "gentle_girl"  (<=16 字符，仅数字/字母/下划线)
language: "zh"  (支持 zh/en/de/it/pt/es/ja/ko/fr/ru)
→ 返回 voice name + preview_audio (Base64)
```

**费用**: 每次创建音色 0.2 元，合成按 0.8 元/万字符计费。

**ArcReel 适用场景**: 根据小说角色描述自动生成对应音色，无需人工选择预设音色

### 5. 实时版本（Realtime）

所有 Qwen3-TTS 系列都有对应的 realtime 版本，通过 WebSocket 双向流式通信。

**WebSocket endpoint**: `wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-tts-flash-realtime`

**输出格式**: pcm（默认）/ wav / mp3 / opus，采样率 8000/16000/24000（默认）/48000

**额外实时参数**（非 realtime 不支持）:
- `speech_rate`: [0.5, 2.0] 语速
- `volume`: [0, 100] 音量
- `pitch_rate`: [0.5, 2.0] 音调
- `bit_rate`: [6, 510] kbps（仅 opus）

**交互模式**:
- `server_commit`（默认）：服务端自动分句并合成
- `commit`：客户端显式 commit 触发合成

**ArcReel 评估**: ArcReel 场景为离线视频生成，非实时模型即可满足需求。实时版本适合后续扩展语音预览/交互场景。

---

## 二、CosyVoice 系列

通义实验室 CosyVoice 系列，超高表现力，多语种支持最广。

### CosyVoice-v3.5-Plus

**model code**: `cosyvoice-v3.5-plus`

**描述**: 对声音克隆和声音设计的语音合成效果进行全面升级，确保说话人高相似度的前提下，支持 free-style 指令控制，合成风格丰富多样。较之前版本大幅减少首包延迟，同时提高发音准确率、改善韵律和音质。

**支持语种**: 中文（含粤语、河南话、闽南话等 10 种方言）、英文、法语、德语、俄语、日语、韩语、葡萄牙语、泰语、印尼语、越南语

**定价**: 1.5 元/万字符，RPM 180

**声音复刻 API**（与 Qwen 系列接口不同）:
- model: `voice-enrollment`（固定）
- action: `create_voice`
- target_model: `cosyvoice-v3.5-plus`
- 返回 `voice_id`（Qwen 返回 voice name）
- 额外参数: `max_prompt_audio_length` [3.0, 30.0] 秒，`enable_preprocess`（降噪/增强）

**限制**: 仅中国内地部署（北京地域）

### CosyVoice-v3.5-Flash

**model code**: `cosyvoice-v3.5-flash`

**定价**: 0.8 元/万字符，3 RPS

与 Plus 相比延迟更低，但表现力稍弱。

**CosyVoice vs Qwen3-TTS 选型**: Qwen3-TTS-Flash 与 CosyVoice-v3.5-Flash 同价（0.8 元/万字符），但 Qwen3-TTS RPM 更高（180 vs 3 RPS）；CosyVoice 多语种覆盖更广（含泰语、印尼语、越南语）。ArcReel 面向越南市场时 CosyVoice 是更好的选择。

---

## 三、系统预设音色

Qwen3-TTS 系列共 48 个系统音色。以下为 ArcReel 场景最相关的音色子集：

### 通用音色（全模型支持）

| voice 参数 | 中文名 | 描述 | 性别 |
|-----------|-------|------|-----|
| `Cherry` | 芊悦 | 阳光正向的自然年轻女声 | 女 |
| `Serena` | 苏瑶 | 温柔的年轻女声 | 女 |
| `Ethan` | 晨煦 | 标准普通话，阳光温暖的年轻男声 | 男 |
| `Chelsie` | 千雪 | 二次元虚拟女友 | 女 |
| `Nofish` | 不吃鱼 | 平翘舌不分的设计师男声 | 男 |

### 叙事/播音类（适合 narration 模式）

| voice 参数 | 中文名 | 描述 | 性别 |
|-----------|-------|------|-----|
| `Jennifer` | 詹妮弗 | 品牌级电影质感美式女声 | 女 |
| `Ryan` | 甜茶 | 充满张力的戏剧男声 | 男 |
| `Bellona` | 燕铮莺 | 强大的叙事者女声 | 女 |
| `Neil` | 阿闻 | 专业新闻主播男声 | 男 |
| `Elias` | 墨讲师 | 学术讲师女声 | 女 |

### 角色扮演类（适合 drama 模式）

| voice 参数 | 中文名 | 描述 | 性别 |
|-----------|-------|------|-----|
| `Momo` | 茉兔 | 俏皮可爱 | 女 |
| `Vivian` | 十三 | 酷飒微辣 | 女 |
| `Moon` | 月白 | 潇洒帅气 | 男 |
| `Maia` | 四月 | 知性温柔 | 女 |
| `Kai` | 凯 | 治愈系男声 | 男 |
| `Katerina` | 卡捷琳娜 | 成熟有气场的女声 | 女 |
| `Bella` | 萌宝 | 可爱小女孩 | 女 |
| `Eldric Sage` | 沧明子 | 智慧长者 | 男 |
| `Vincent` | 田叔 | 沙哑老练 | 男 |

### 方言/外语音色

| voice 参数 | 语言/方言 | 性别 |
|-----------|---------|-----|
| `Jada` | 上海话 | 女 |
| `Dylan` | 北京话 | 男 |
| `Sunny` / `Eric` | 四川话 | 女/男 |
| `Rocky` / `Kiki` | 粤语 | 男/女 |
| `Aiden` | 美式英语 | 男 |
| `Bodega` | 西班牙语 | 男 |
| `Ono Anna` | 日语 | 女 |
| `Sohee` | 韩语 | 女 |

> 完整 48 音色列表见官方文档。并非所有音色支持所有模型，Cherry / Ethan / Nofish 覆盖最广。

---

## 四、ArcReel 集成建议

### 推荐方案

1. **标准 TTS（narration 模式朗读）**: `qwen3-tts-flash`，voice 选 `Cherry`（女）或 `Ethan`（男）或 `Ryan`（戏剧张力）
2. **情感控制（drama 模式台词）**: `qwen3-tts-instruct-flash`，通过 instructions 参数按角色情感动态调整
3. **角色声音定制**: `qwen3-tts-vd`（声音设计），从角色描述文本自动生成音色；或 `qwen3-tts-vc`（声音复刻），从参考音频复刻
4. **越南语场景**: `cosyvoice-v3.5-plus`，唯一支持越南语的方案

### API 统一接入

所有 Qwen3-TTS 非实时模型统一走 `dashscope.MultiModalConversation.call()` 接口（DashScope SDK >= 1.23.1），endpoint:
- 中国内地: `https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`
- 国际: `https://dashscope-intl.aliyuncs.com/api/v1/...`

CosyVoice 走旧版 `dashscope.audio.SpeechSynthesizer` 接口。

声音复刻/设计统一走: `POST /api/v1/services/audio/tts/customization`

### 费用预估

以单集 5 分钟视频、约 1500 字旁白为例：
- `qwen3-tts-flash`: 1500 / 10000 * 0.8 = **0.12 元/集**
- `cosyvoice-v3.5-plus`: 1500 / 10000 * 1.5 = **0.225 元/集**
- `MiniMax/speech-2.8-hd`: 1500 / 10000 * 3.5 = **0.525 元/集**（不推荐）

### 不收录的模型

| 模型 | 原因 |
|------|------|
| MiniMax/speech-2.8-hd | 三方直供，语音合成 3.5 元/万字符（4.4 倍于 Qwen3），声音复刻 9.9 元/次，RPM 仅 20 |
| Sambert（sambert-zhiyuan-v1） | 旧模型（2025-03），官方建议新项目使用 CosyVoice 或 Qwen-TTS |
| 大模型声音复刻及声音设计（voice-enrollment） | 旧版统一入口，已被 qwen3-tts-vc/vd 和 cosyvoice 各自的专用模型替代 |
| 音乐生成（fun-music-preview） | 音乐生成非 TTS 范畴 |

---

## 数据来源

- 百炼控制台模型市场（https://bailian.console.aliyun.com）各模型详情页
- 官方文档：非实时语音合成（https://help.aliyun.com/zh/model-studio/qwen-tts）
- 官方文档：实时语音合成（https://help.aliyun.com/zh/model-studio/qwen-tts-realtime-api-reference/）
- 官方文档：声音复刻 API（https://help.aliyun.com/zh/model-studio/qwen-tts-voice-cloning）
- 官方文档：声音设计 API（https://help.aliyun.com/zh/model-studio/qwen-tts-voice-design）
- 截至 2026-06-02 核实
