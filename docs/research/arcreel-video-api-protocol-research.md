# ArcReel 视频 API 协议适配调研报告

**调研截止日期**：2026-05-27
**用途**：作为后续 PRD 与设计文档撰写的输入素材
**作者**：协助调研（Claude）
**关联背景**：ArcReel 视频供应商体系扩展、自定义供应商生态对接

---

## 0. 调研范围与定位

本报告是**调研性质**的素材汇编，**不包含**具体的目录结构、Adapter 类设计、实施计划。这些内容应在后续 PRD 和设计文档阶段，基于 ArcReel 当前 `lib/video_backends/` + `lib/custom_provider/` 架构产出。

调研覆盖的问题：
1. 中转站视频 API 端口格式有哪些事实标准
2. 主流官方视频生成平台的 API 规格
3. 各协议的能力支持矩阵（文生 / 图生 / 首尾帧 / 参考生 / 视频续写 / 音频 / 对口型 / 角色一致性）
4. 各协议的可行性评估和优先级建议
5. 最优先协议的完整 API 规格细节
6. 自定义供应商接入方案选型（plugin vs 声明式）
7. 运行时 plugin 机制的可行性与设计选项（支撑已规划的社区化协议分享功能）

不在调研范围：
- ArcReel 代码层面的目录组织 / 类继承 / 文件命名
- 数据库 schema 变更 / Alembic 迁移
- 前端 UI 改动
- 具体的实施时间线

---

## 1. ArcReel 架构调研基线（重要：已对齐现状）

> 这一节是**理解后续调研结论的前提**，所有协议适配建议都必须套在这套架构上。

### 1.1 视频 backend 抽象层

ArcReel 已在 `lib/video_backends/` 建立成熟的视频生成抽象：

| 关键元素 | 位置 | 说明 |
|---|---|---|
| `VideoBackend` Protocol | `lib/video_backends/base.py` | 鸭子类型契约，要求 `name` / `model` / `capabilities` / `generate()` |
| `VideoCapability` 枚举 | `lib/video_backends/base.py` | 能力位图：`TEXT_TO_VIDEO` / `IMAGE_TO_VIDEO` / `GENERATE_AUDIO` / `NEGATIVE_PROMPT` / `VIDEO_EXTEND` / `SEED_CONTROL` / `FLEX_TIER` |
| `VideoGenerationRequest` / `VideoGenerationResult` | 同上 | 统一请求/响应数据类 |
| `register_backend(name, factory)` | `lib/video_backends/registry.py` | 注册机制 |
| 已有 backend 实现 | `gemini.py` / `ark.py` / `grok.py` / `openai.py` / `newapi.py` / `vidu.py` | 6 家供应商 |

**ArcReel 词汇表约定**：用 **backend**（按 provider + model 构造、真正调用 API 的客户端对象）指代生成后端，术语表 `_Avoid` 标注避免使用 `adapter` 一词。其本质是 Ports & Adapters 范式中的 Adapter 角色，但 ArcReel 统称 backend 以保持与 provider 派生语义、frontend 对仗、三套媒体后端的命名一致（与 SQLAlchemy / Django 用 backend 命名同类角色的惯例一致）。架构对齐的讨论见 9.1。

### 1.2 自定义供应商体系

`lib/custom_provider/` 已经支持用户接入任意 OpenAI/Google 兼容的中转站：

| 关键元素 | 作用 |
|---|---|
| `CustomProvider` ORM（DB 表） | `discovery_format` ∈ {openai, google} + `base_url` + `api_key` |
| `CustomProviderModel` ORM（DB 表） | 每个模型挂一个 `endpoint`（ENDPOINT_REGISTRY key） |
| `ENDPOINT_REGISTRY` | 协议归属的单一真相源 |
| `infer_endpoint()` | 启发式从 model_id 推断 endpoint |
| `create_custom_backend()` factory | 按 endpoint 派发到对应 VideoBackend |
| 前端 UI | 在设置页 CRUD，自动 `/v1/models` 发现 |

**当前 ENDPOINT_REGISTRY 已有 6 条**（不含 anthropic-messages 等本轮调研不涉及的）：

| Endpoint Key | media_type | family | 状态 |
|---|---|---|---|
| `openai-chat` | text | openai | 已实现 |
| `gemini-generate` | text | google | 已实现 |
| `openai-images` | image | openai | 已实现 |
| `gemini-image` | image | google | 已实现 |
| `openai-video` | video | openai | 已实现（**OpenAI Sora `/v1/videos` 兼容**） |
| `newapi-video` | video | newapi | 已实现（**NewAPI `/v1/video/generations` 自有协议**） |

### 1.3 预置供应商

`lib/config/registry.py` 的 `PROVIDER_REGISTRY` 已有 5 家预置：

- `gemini-aistudio` / `gemini-vertex` — Veo 3.1 全系
- `ark` — 火山方舟（Seedance 1.5 Pro / 2.0 / 2.0 Fast 已注册）
- `grok` — xAI Grok Imagine Video
- `openai` — Sora 2 / Sora 2 Pro
- `vidu` — Vidu Q3 全系（含参考生视频）

### 1.4 协议绑定到模型级别（架构决策）

ArcReel 已在 `2026-04-26-custom-provider-model-endpoint-design` 中明确确认：

> 一个中转站 = 一个 provider；同一个 provider 下不同模型可以走完全不同的协议；
> 协议归属下沉到模型层（`CustomProviderModel.endpoint` 字段）；
> provider 层 `discovery_format` 只用于模型发现，与调用协议解耦。

**所有后续协议适配设计必须遵守这一架构约束。**

---

## 2. 中转站协议生态格局（事实标准归纳）

调研覆盖了 15+ 主流中转站（NewAPI 主仓库、AiHubMix、七牛云、Wisdom Gate、AI/ML API、APIMart、EvoLink、CometAPI、Kie.ai、PiAPI、useapi.net、kazhang.ai、apiyi、burn.hair、closeai 等）后，归纳出**四大流派并存**的事实：

### 2.1 流派 A：OpenAI Sora `/v1/videos`

**端点**：`POST /v1/videos` + `GET /v1/videos/{video_id}` + `GET /v1/videos/{video_id}/content`
**形态**：multipart/form-data 或 JSON
**参数**：`model` + `prompt` + `seconds`（字符串）+ `size`（"1280x720"）+ `input_reference`
**鉴权**：`Authorization: Bearer`
**代表实例**：OpenAI 官方、AiHubMix、七牛云 sora 路径、Wisdom Gate、Azure OpenAI、ArcReel 已有 `openai-video` endpoint

**关键事实**：
- OpenAI 官方公告 **Sora 2 / Sora 2 Pro 将于 2026-09-24 退役**（developer notification 2026-03-24 发出）
- 路径事实标准会保留，中转站惯性沿用承载其他模型
- 七牛云兼容路径在 query 响应里直接返回 `task_result.videos[0].url`，而 OpenAI 官方需二次 GET `/content`

### 2.2 流派 B：NewAPI 自有 `/v1/video/generations`

**端点**：`POST /v1/video/generations`（注意复数）+ `GET /v1/video/generations/{task_id}`
**形态**：纯 JSON
**参数**：`model` + `prompt` + `image` + `duration`（数字）+ `width`/`height` + `metadata`（对象）
**鉴权**：`Authorization: Bearer`
**代表实例**：所有基于 NewAPI / OneAPI 部署的中转站（DMXAPI、closeai、burn.hair 等数十家）；ArcReel 已有 `newapi-video` endpoint

**关键事实**：
- `metadata{}` 字段是 vendor-specific 透传的黑盒（NewAPI 文档明确列出的只有 Kling 的 `image_tail`/`negative_prompt`/`seed` 和 Jimeng 的 `req_key`/`image_urls`/`aspect_ratio`，其他完全靠中转站中间件）
- 中转站之间 metadata 透传完整度差异极大，同一个 Kling `camera_control` 在 DMXAPI 能用、在某些自部署 NewAPI 上丢失
- 状态机：`queued / in_progress / completed / failed`

### 2.3 流派 C：v2 通用 generations（一个端点全部模型）

**端点**：`POST /v2/video/generations` 或 `/v2/videos/generations`（各家细微差异）
**形态**：纯 JSON
**核心特征**：**单端点 + model 字段切换**，承载几十上百个模型（Kling/Veo/Sora/Hailuo/Wan/Seedance 全在一个 URL 下）
**鉴权**：`Authorization: Bearer`
**代表实例**：
- **AI/ML API**（aimlapi.com）`/v2/video/generations` — 流派 C 最典型代表
- **getimg.ai** `/v2/videos/generations`
- **APIMart** `/v1/videos/generations`
- **EvoLink** `/v1/videos/generations`
- **七牛云 veo** 路径 `/v1/videos/generations`
- **xAI 官方** `/v1/videos/generations`（**官方就是这个形态**）
- **CometAPI** `/volc/v3/contents/generations/tasks`（路径细节有差异但思想一致）

**关键事实**：
- 实际是 **discriminated union by model**：单 URL 接受 60+ model id，但每个 model 的可选字段集合完全不同
- 例：同一端点下 Kling 接 `cfg_scale` / `negative_prompt` / `image_url` / `last_image_url`；Veo 3.1 接 `image_urls[]`；Seedance 接 `reference_images[]` / `reference_audios[]` / `reference_videos[]` / `generate_audio`；Sora 接 `resolution` / `image_url`
- model 命名碎片化严重：AIMLAPI 用 `kling-video/v1/standard/text-to-video`，APIMart 用 `sora-2-vip`，getimg.ai 用 `happyhorse-1`（自有品牌名）
- **被很多中转站采用**，是流派 B 之外的主流事实标准
- 在 ArcReel 当前 ENDPOINT_REGISTRY 中**尚未覆盖**

### 2.4 流派 D：动词 create/submit 风格

**端点**：`POST /api/v1/jobs/createTask`、`POST /api/v1/task`、`POST /v1/videos/create` 等（create + query 拆分）
**形态**：纯 JSON，参数嵌套在 `input{}` 子对象
**代表实例**：
- **Kie.ai** `/api/v1/jobs/createTask` + `/api/v1/jobs/recordInfo`
- **PiAPI** `/api/v1/task` + `/api/v1/task/{task_id}`
- **useapi.net** `/v1/{vendor}/videos/create`
- **速创** `/api/sora2/submit`
- **laozhang.ai** `/veo/v1/api/video/submit`

**关键事实**：
- 流派 D 内部不统一，每家路径和参数细节差异巨大，无法用一个 backend 复用
- PiAPI 有非标状态码（外层 `status: "completed"` 字符串 + 内层 `output.status: 99` 整数表完成），需双层判断

### 2.5 流派分布观察

按调研覆盖的中转站数量统计（**非严格市场占有率**）：

- 流派 A（OpenAI Sora 兼容）：覆盖度高，海外/合规客户必备
- 流派 B（NewAPI 自有）：**国内自部署中转站事实垄断**，覆盖度最高
- 流派 C（v2 通用 generations）：**被很多中转站采用**，海外聚合站和 xAI 官方采用，覆盖度位居第二
- 流派 D（动词风格）：内部分散，各家自定义为主，单家覆盖度有限

---

## 3. 主流官方视频平台 API 调研

### 3.1 已被 ArcReel 接入的平台

| 平台 | 当前状态 | 协议归属 | 备注 |
|---|---|---|---|
| Google Veo 3.1 | 已实现 | Vertex AI + AI Studio 双后端 | 文生/图生/续写/音频/负面提示 |
| 火山 Ark Seedance 2.0 | 已实现 | Ark SDK | 2.0 已注册但**未启用多模态参考、视频延长** |
| xAI Grok Imagine | 已实现 | xAI SDK | 文生/图生/参考生 |
| OpenAI Sora 2 | 已实现 | openai SDK | 含 `create_and_poll` |
| Vidu Q3 | 已实现 | httpx | 文生/图生/**参考生视频**（已应用于 reference-to-video 模式） |

### 3.2 调研但尚未接入的官方平台

#### 可灵 Kling 官方

- **Base URL**：`https://api.klingai.com`
- **鉴权**：JWT HS256，payload `{iss: ak, exp: now+1800, nbf: now-5}`，30 分钟过期
- **端点**：按 intent 多端点 — `/v1/videos/text2video`、`/image2video`、`/multi-image2video`、`/video-extend`、`/lip-sync`
- **能力**：T2V / I2V / FLF（pro 模式 `image_tail`）/ R2V（`elements[]` 1.6）/ Extend / Audio（2.6 pro `enable_audio`）/ LipSync
- **关键坑点**：
  - status 字符串是 `succeed` 不是 `succeeded`
  - 视频 URL 在 `$.data.task_result.videos[0].url`
  - 500 表示内容审核拒绝（用 error 字段判断，不是 message）
  - `kling-2.1-master` 不支持 `mode` 字段；2.x 模型不支持 `cfg_scale`
- **网文场景价值**：国内最强视频模型之一，工作室自部署必备直连选项

#### 阿里 DashScope 通义万相

- **Base URL（北京）**：`https://dashscope.aliyuncs.com/api/v1`
- **Base URL（新加坡）**：`https://dashscope-intl.aliyuncs.com/api/v1`
- **鉴权**：`Authorization: Bearer` + 强制 `X-DashScope-Async: enable` header
- **端点**：`POST /services/aigc/video-generation/video-synthesis`（wan2.5+ 多模态）/ `POST /services/aigc/image2video/video-synthesis/`（wan2.1 / wan2.2-s2v）+ `GET /tasks/{task_id}`
- **能力**：T2V / I2V / FLF（wan2.1-kf2v）/ R2V（`ref_image_urls[]`）/ Extend / Audio / 数字人 LipSync（wan2.2-s2v）
- **同协议覆盖多模型**：wan2.7-t2v / wan2.6-i2v-flash / wan2.1-kf2v-plus / wan2.2-s2v
- **关键坑点**：
  - 漏 `X-DashScope-Async: enable` 会同步等死
  - wan2.7 用 `resolution`+`ratio`，wan2.6 用 `size`（"1280*720"，星号）
  - 状态机全大写：`PENDING / RUNNING / SUCCEEDED / FAILED / CANCELED / SUSPENDED / UNKNOWN`
  - OSS 临时 URL **24h** 有效期，task_id 也是 24h
  - `IPInfringementSuspect` / `DataInspectionFailed` 是内容审核错误
- **网文场景价值**：
  - wan2.7 中文 prompt 长度极长，单次可塞下完整分镜脚本（5 镜头 1500 字 OK）
  - `shot_type: multi` 原生多镜头叙事

#### MiniMax Hailuo 官方

- **Base URL（全球）**：`https://api.minimax.io/v1`
- **Base URL（国内）**：`https://api.minimaxi.com/v1`
- **鉴权**：`Authorization: Bearer`
- **端点**：`POST /video_generation` + `GET /query/video_generation?task_id={id}` + **`GET /files/retrieve?file_id={id}`**（两步取 URL）
- **能力**：T2V / I2V / FLF / R2V（S2V-01 的 `subject_reference`）/ Audio（Hailuo 2.3）/ Director 模型支持 prompt 内嵌镜头指令
- **关键坑点**：
  - **两步取 URL**：query 不返回视频 URL，只返回 `file_id`，需再调 File API
  - **下载 URL 仅 9 小时有效**（32,400 秒，官方公告原文）— 全平台最短
  - status 首字母大写：`Preparing / Queueing / Processing / Success / Fail`
  - Webhook 注册需 echo `challenge`，3 秒超时
  - 国内 / 全球 endpoint 不同，API Key 独立
  - `base_resp.status_code != 0` 表示业务错误（HTTP 200 也可能内部失败）
- **网文场景价值**：性价比之王，物理表现强

#### Runway Gen-3 / Gen-4 / Gen-4.5

- **Base URL**：`https://api.dev.runwayml.com/v1`
- **鉴权**：`Authorization: Bearer` + 强制 `X-Runway-Version: 2024-11-06` header
- **端点**：`/image_to_video` / `/text_to_video` / `/video_to_video` + `/tasks/{id}`
- **能力**：T2V（gen4.5）/ I2V / R2V（gen4_image `@mention`）/ Aleph 视频编辑 / Act-Two LipSync
- **关键坑点**：
  - 无 webhook，必须轮询
  - 文件上传走 ephemeral upload URI
  - duration 仅 `5 | 10`
- **业务价值**：海外广告/电商需求强，Gen-4.5 在 Arena 第 1

#### Luma Dream Machine (Ray-2)

- **Base URL**：`https://api.lumalabs.ai/dream-machine/v1`
- **鉴权**：`Authorization: Bearer`
- **端点**：**单一 `POST /generations`** + `GET /generations/{id}`
- **能力**：T2V / I2V / FLF / Extend / Loop（**用 `keyframes.frame0/frame1` 双槽位优雅表达所有 intent**）
- **关键设计**：keyframes 联合类型 `{type: "image"|"generation", url|id}`，frame0 用 generation id 即 Extend
- **关键坑点**：keyframes 联合类型必须特殊建模，不能复用 `image_url` 字段

#### Pika 2.2

- **官方 API 不自助**，仅对 B2B partner 开放
- **唯一接入路径**：通过 fal.ai `queue.fal.run/fal-ai/pika/v2.2/{capability}`
- **独家能力**：Pikaframes（2-5 keyframes 多帧插值）、Pikascenes（多图融合，`ingredients_mode: creative|precise`）

#### PixVerse

- **Base URL**：`https://app-api.pixverse.ai`
- **鉴权**：`API-KEY:` 自定义 header + 强制 `Ai-trace-id` 幂等键
- **端点**：`/openapi/v2/video/{text|img|transition|extend|fusion|lipsync}/generate`
- **能力**：T2V / I2V / FLF（Transition）/ R2V（Fusion）/ Extend / Audio（V5.5+）/ LipSync
- **NewAPI 无原生 channel**，必须官方直连

#### 字节即梦 Jimeng（火山引擎 CV）

- **Base URL**：`https://visual.volcengineapi.com`
- **鉴权**：**AWS V4 风格签名**（火山 SigV4）
- **端点**：`?Action=CVSync2AsyncSubmitTask` / `?Action=CVSync2AsyncGetResult`
- **复杂度极高**，建议走中转层而非直连

#### fal.ai

- **Base URL**：`queue.fal.run/{vendor}/{model}/{capability}`
- **鉴权**：fal.ai Key
- **特点**：原生 Webhook 支持（`?fal_webhook=URL`）+ JWKS 签名验证
- **价值**：是 Pika 2.2 的唯一可行接入路径；同时可接 Luma / Veo / Kling 等多模型

---

## 4. 能力矩阵（横向对比）

### 4.1 能力维度定义

- **T2V**：文生视频，仅 prompt
- **I2V 首帧**：图生视频（首帧约束）
- **FLF**：首尾帧（First-and-Last-Frame）
- **R2V**：参考生视频（多张参考图保持角色/物体一致性，**不是首帧**）
- **Extend**：视频续写/扩展
- **Audio**：原生音视频联合生成
- **LipSync**：对口型
- **Character**：角色一致性（命名实体注册）

### 4.2 中转站流派 × 能力

✅ 原生 ｜ 🟡 部分支持/限定模型 ｜ ❌ 不支持

| 流派 / 平台 | T2V | I2V | FLF | R2V | Extend | Audio | LipSync | Character |
|---|---|---|---|---|---|---|---|---|
| **流派 A** OpenAI Sora `/v1/videos` | ✅ | ✅ | ❌ | 🟡 characters API | ✅ extensions | ✅ | ❌ | ✅ |
| **流派 B** NewAPI `/v1/video/generations` | ✅ | ✅ | 🟡 metadata.image_tail | 🟡 metadata 透传 | ❌ | 🟡 透传 | ❌ | ❌ |
| **流派 C** AIMLAPI `/v2/video/generations` | ✅ | ✅ | ✅ | ✅ | 🟡 vendor-specific | ✅ | ❌ | ✅ |
| **流派 C** APIMart `/v1/videos/generations` | ✅ | ✅ | ❌ | 🟡 prompt-mention | 🟡 sora ref | ✅ Sora2 | ❌ | 🟡 prompt-mention |
| **流派 D** Kie.ai `jobs/createTask` | ✅ | ✅ | 🟡 model-specific | ✅ character_id_list ≤5 | 🟡 veo extend | ✅ veo/sora2/wan2.7 | ❌ | ✅ |
| **流派 D** PiAPI `task` | ✅ | ✅ | ✅ image_tail_url | ✅ elements | ✅ task_type=extend | ✅ Seedance 2.0 | ✅ 独立 task | ✅ |

### 4.3 官方平台 × 能力

| 平台 | T2V | I2V | FLF | R2V | Extend | Audio | LipSync | Character |
|---|---|---|---|---|---|---|---|---|
| OpenAI Sora 2 | ✅ | ✅ | ❌ | 🟡 characters | ✅ 6×20s=120s | ✅ | ❌ | ✅ |
| Google Veo 3.1 | ✅ | ✅ | ✅ | ✅ | 🟡 /extend | ✅ 48kHz | ❌ | 🟡 |
| 火山 Seedance 2.0 | ✅ | ✅ first_frame | ✅ last_frame | ✅ 9 图 + 3 视频 + 3 音频 | ✅ | ✅ | ✅ phoneme 8 语言 | ✅ omni-ref |
| 可灵 Kling 2.6 / 3.0 | ✅ | ✅ | ✅ pro 模式 | ✅ elements ≤4 | ✅ extend | ✅ 2.6 pro | ✅ lip-sync task | ✅ multi-image |
| MiniMax Hailuo 02 / 2.3 | ✅ | ✅ | ✅ | ✅ subject_reference | ❌ | ✅ 2.3 | ❌ | ✅ S2V-01 |
| PixVerse V5.5 / V6 | ✅ | ✅ | ✅ Transition | ✅ Fusion | ✅ extend | ✅ V5.5+ | ✅ | ✅ ≤3 图融合 |
| Vidu Q3 | ✅ | ✅ | ✅ | ✅ ref_image_urls ≤7 | 🟡 | ✅ + bgm | ❌ | ✅ |
| Runway Gen-4.5 | ✅ | ✅ | ❌ | ✅ gen4_image @mention ≤3 | 🟡 expand | ❌ | ✅ Act-Two | ✅ |
| Pika 2.2 | ✅ | ✅ | ✅ Pikaframes | ✅ Pikascenes ≤5 | 🟡 | ❌ | ❌ | ✅ |
| Luma Ray-2 | ✅ | ✅ keyframes.frame0 | ✅ frame0+frame1 | ❌ | ✅ generation id | ❌ | ❌ | 🟡 chain |
| 阿里 Wan 2.7 | ✅ | ✅ | ✅ | ✅ Image1..5 占位 | ✅ | ✅ | ✅ s2v 数字人 | ✅ |
| xAI Grok Imagine | ✅ | ✅ image:{url} | ❌ | ✅ reference_images ≤7 | ✅ extensions | ❌ | ❌ | 🟡 |

### 4.4 关键观察

- **R2V 字段名共 7 种命名**：`characters` / `reference_images` / `image_urls` / `reference_image_urls` / `elements` / Wan 2.7 prompt 占位符 / `subject_reference`
- **FLF 字段名共 4 种**：`last_frame_image` / `image_tail` / `last_image_url` / `keyframes.frame1`
- **duration 表达**全平台不一：字符串 `"5"` / 整数 `5` / 帧数 / 枚举 `6|10` / Luma `"5s"` / Sora `"4"|"8"|"12"|"16"|"20"` 字符串枚举
- **音视频同步生成**语义重叠：联合生成（Veo/Sora/Seedance/Vidu Q3）vs 显式开关（Kling 2.6 pro `enable_audio` / Vidu `generate_audio`）vs 自动（Hailuo 2.3）

---

## 5. 参数对齐表（横向 15 维度）

| 维度 | OpenAI Sora 2 | xAI Grok | Kling | MiniMax | Veo 3.1 | Wan 2.7 | Runway | Luma | Vidu Q3 | Pika 2.2 | Seedance 2.0 | NewAPI 流派 B | aimlapi 流派 C | Kie.ai 流派 D | PiAPI 流派 D |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **prompt** | `prompt` | `prompt` | `prompt` | `prompt` | `prompt` | `prompt` | `promptText` | `prompt` | `prompt` | `prompt` | `content[].text` | `prompt` | `prompt` | `input.prompt` | `input.prompt` |
| **negative_prompt** | — | — | `negative_prompt` | — | `negative_prompt` | `negative_prompt` ≤500 | — | — | — | `negative_prompt` | — | `metadata.negative_prompt` | `negative_prompt` | `input.negative_prompt` | `input.negative_prompt` |
| **duration** | `seconds: "4\|8\|12\|16\|20"` | `duration: 1-15` | `duration: "5"\|"10"` | `duration: 6\|10` | `seconds: "8"` | `duration: 2-15` | `duration: 5\|10` | `duration: "5s"\|"9s"` | `duration: 1-16` | `duration: 5\|10` | `duration: 4\|5\|6\|8\|10\|12\|15` | `duration: 5` int | `duration` 字符串 | `input.n_frames`/`input.duration` | `input.duration: 5` |
| **resolution** | `size: "1280x720"` | `resolution: "720p"` | `aspect_ratio` | `resolution: "1080P"` | `aspectRatio` | `resolution: "720P"` + `ratio` | `ratio: "1280:720"` | `resolution: "540p..4k"` | `resolution: "540p"\|"720p"\|"1080p"` | `aspect_ratio` + `resolution` | `ratio: "16:9"` + `resolution` | `size: "1920x1080"` | model decides | `input.size: "standard"\|"hd"` | `input.aspect_ratio` |
| **seed** | — | **无** | (旧版) | — | — | `seed` | `seed` | — | `seed: -1` random | `seed` | `seed` | `metadata.seed` | `seed` | — | `input.seed` |
| **首帧** | `input_reference` | `image:{url}` | `image_url` | `first_frame_image` | `image_url` | `first_frame_url` | `promptImage` | `keyframes.frame0` | `image` | `image_url` | `first_frame_url` | `image` | `image_url` | `input.image_urls[0]` | `input.image_url` |
| **尾帧** | ❌ | ❌ | `image_tail` | `last_frame_image` | ❌ | `last_frame_url` | ❌ | `keyframes.frame1` | `last_image` | `images[1]` (Pikaframes) | `last_frame_url` | `metadata.image_tail` | `last_image_url` | (部分 model) | `input.image_tail_url` |
| **参考数组** | `characters:[{id}]` | `reference_images:[{url}]` ≤7 | `elements:[]` ≤4 | `subject_reference` 单 | `image_urls:[]` | `Image1..5` 占位 | `referenceImages` ≤3 | — | `reference_image_urls:[]` ≤7 | `images:[]` ≤5 | `reference_images:[]` ≤9 | `metadata.image_urls:[]` | `image_urls`/`reference_images` | `input.character_id_list` | `input.elements:[]` |
| **音频开关** | (自动) | — | `enable_audio` (2.6 pro) | (2.3 自动) | (自动) | (自动) | — | — | `generate_audio` + `bgm` | — | `generate_audio` | (透传) | (model 决定) | `input.sound` (2.6) | `input.enable_audio` |
| **回调** | — 轮询 | — 轮询 | 轮询 | 轮询 | LRO | DashScope async | 轮询 | 轮询 | 轮询 | 轮询 | `callback_url` | `webhook_url`（部分） | 轮询 | `callBackUrl` | `config.webhook_config` |
| **模式/质量** | model 决定 | — | `mode: "std"\|"pro"` | `prompt_optimizer` | model variant | — | model variant | model variant | `movement_amplitude` | `ingredients_mode` | model variant `-fast` | — | model id 含 mode | `input.size: "standard"\|"hd"` | `input.mode: "std"\|"pro"` |
| **CFG** | — | — | `cfg_scale: 0-1` (v1) | — | — | — | — | — | — | `cfg_scale` (legacy) | — | (透传) | `cfg_scale` | — | `input.cfg_scale` |
| **特殊 header** | — | — | JWT 30min | — | OAuth2 | `X-DashScope-Async` | `X-Runway-Version` | — | — | — | — | — | — | — | — |
| **task_id 路径** | `$.id` | `$.request_id` | `$.data.task_id` | `$.task_id` | LRO operation | `$.output.task_id` | `$.id` | `$.id` | `$.task_id` | `$.request_id` | `$.id` | `$.task_id` | `$.id` | `$.data.taskId` | `$.task_id` |
| **视频 URL 路径** | 二次 GET /content | `$.video.url` | `$.data.task_result.videos[0].url` | 二次 file_id | `$.video.uri` | `$.output.video_url` | `$.output[0]` | `$.assets.video` | `$.creations[0].url` | `$.video.url` | `$.content.video_url` | `$.url` | `$.video.url` / `$.assets.video` | `$.data.response.resultUrls[0]` | `$.output.works[0].video.url` |

### 5.1 状态机映射差异

各家终态字符串完全不一致，必须各家映射：

| 平台 | queued | running | succeeded | failed |
|---|---|---|---|---|
| OpenAI Sora | `queued` | `in_progress` | `completed` | `failed` |
| NewAPI 流派 B | `queued` | `in_progress` | `completed` | `failed` |
| 阿里 DashScope | `PENDING`（**全大写**） | `RUNNING` | `SUCCEEDED` | `FAILED` |
| 火山 Ark Seedance | `queued` | `running` | `succeeded` | `failed` |
| 可灵 Kling | `submitted` | `processing` | **`succeed`（不是 succeeded）** | `failed` |
| MiniMax Hailuo | `Queueing` / `Preparing` | `Processing` | **`Success`（首字母大写）** | `Fail` |
| PiAPI 内层 | — | — | `output.status: 99`（**整数**） | `output.status: <99` |
| Runway | `PENDING` | `RUNNING` | `SUCCEEDED` | `FAILED` |

### 5.2 视频 URL 过期时间（OSS 临时链接）

**所有官方平台均使用临时 URL，必须立即转存**：

| 平台 | 过期时间 | 风险等级 |
|---|---|---|
| **MiniMax Hailuo** | **9 小时**（32,400 秒） | 🔴 全平台最短 |
| OpenAI Sora 2 | 文档与实测不符（文档 24h，部分用户报告 1h） | 🟠 |
| Vertex AI Veo | LRO operation 完成后短期 | 🟠 |
| 火山 Ark Seedance | 24 小时（TOS 临时 URL） | 🟡 |
| 阿里 DashScope Wan | 24 小时 | 🟡 |
| Runway | 24-48 小时 | 🟡 |
| Luma Ray-2 | 较长 | 🟢 |
| 可灵 Kling | 30 天（推断自 lip-sync 端点约束） | 🟢 |
| 七牛云 sora 兼容 | 7 天 | 🟢 |

---

## 6. 协议优先级建议

> 优先级评分维度（每项 1-5）：中转站生态覆盖度 / 模型能力覆盖 / 文档完整性 / 实现复杂度（越简单越高）/ 长期稳定性 / ArcReel 网文场景需求度

### 6.1 已实现协议（保持）

| 协议 | ArcReel endpoint | 状态 | 备注 |
|---|---|---|---|
| OpenAI Sora 兼容 | `openai-video` | ✅ 已实现 | 注意 2026-09-24 deprecation |
| NewAPI 自有 | `newapi-video` | ✅ 已实现 | metadata 透传需扩展 vendor 映射表 |
| Veo 3.1 全系 | gemini-* provider | ✅ 已实现 | — |
| Seedance 1.5/2.0 | ark provider | ✅ 已实现 | 2.0 多模态参考 + Extend 未启用 |
| Grok Imagine | grok provider | ✅ 已实现 | — |
| Vidu Q3 全系 | vidu provider | ✅ 已实现 | reference-to-video 模式已上线 |

### 6.2 P0 — v1.0 建议补齐

| 协议 | 加入 P0 理由 | 复杂度 |
|---|---|---|
| **流派 C `/v2/video/generations`** | **被很多中转站采用**（AIMLAPI / xAI 官方 / getimg.ai / APIMart / EvoLink / 七牛 veo / CometAPI 等），是流派 B 之外的第二大事实标准 | 中（discriminated union by model，需 model-specific schema 分支） |
| **可灵 Kling 官方协议** | 国内小说转视频用户首选模型；工作室自部署需要直连保证稳定性；JWT 鉴权特殊 | 中-高（JWT HS256 + 30min token cache，按 intent 多端点） |
| **阿里 DashScope（通义万相 Wan）** | 网文场景**独特价值**：wan2.7 中文长 prompt + `shot_type: multi` 多镜头叙事原生支持；同协议覆盖 wan2.7/2.6/2.1-kf2v/2.2-s2v 多模型 | 中（X-DashScope-Async + input/parameters 嵌套 + 多 model 路由） |
| **MiniMax Hailuo 官方** | 物理表现强、性价比之王；File API 两步取 URL 模型与其他平台不同；9h URL 转存最紧急 | 中-高（File API 两步流程 + webhook challenge） |

### 6.3 P1 — v1.x 补充

| 协议 | 理由 |
|---|---|
| **Kie.ai 流派 D** `/api/v1/jobs/createTask` | 海外动词风格代表，可覆盖部分小众中转站 |
| **Runway Gen-3/4 官方** | 广告/电商需求；`X-Runway-Version` header；无 webhook 必轮询 |
| **Seedance 2.0 多模态参考扩展** | 已注册模型但未启用 reference_images / video_extend，提升现有 ark provider 能力位图 |

### 6.4 P2 — 等用户反馈

| 协议 | 理由 |
|---|---|
| PixVerse 官方 | NewAPI 无原生 channel，必须官方直连；`API-KEY` 自定义 header |
| fal.ai queue API | 是 Pika 2.2 唯一接入路径；统一队列协议价值 |
| Luma Dream Machine | keyframes 联合类型独特，但中文场景需求度低 |

### 6.5 P3 — 不主动做

| 协议 | 理由 |
|---|---|
| 字节即梦 Jimeng 直连 | 火山 SigV4 签名复杂度极高；建议走中转层（流派 B/C 普遍代理） |
| PiAPI 单独适配 | 与 Kie.ai 重叠度高，覆盖到 P1 即可 |
| Replicate predictions | ArcReel 用户群体重叠度低 |
| Together AI | 同上 |

---

## 7. 自定义供应商接入方案选型（结论）

### 7.1 选型对比

经过详细评估，**Plugin（Python 代码扩展）方案优于声明式 YAML 方案**：

| 维度 | Python plugin | 声明式 YAML | 谁赢 |
|---|---|---|---|
| 协议覆盖率 | 100%（兜底） | 50-60%（JWT/SigV4/multipart/两步流程无法表达） | plugin |
| 接入难度 | 写 backend 类 + 测试 | 写 YAML + 调试模板 | 表面打平，实际 plugin 更易调试 |
| ArcReel 维护成本 | 每个 backend 独立，已有体系 | 需要新增 Jinja2 + JSONPath + state mapping + probe + 文档 | plugin（重大优势） |
| 调试体验 | 标准 Python traceback | YAML schema 错误 / 模板渲染错误 / 字段路径错误 | plugin 大胜 |
| AI 辅助代码生成 | Cursor/Claude Code 30 秒出完整 backend | 需要先理解自创 YAML schema | plugin（2024+ 关键反转） |
| ArcReel 用户画像匹配度 | 全员开发者，会 Python | 适合非程序员 | plugin |

### 7.2 关键决策依据

**视频 API 生态有 10 类能力必须 Python 实现**（声明式无法表达）：

1. Kling 官方 JWT 签名（HS256，30 分钟过期，需 token cache）
2. 火山 Ark SigV4-like 签名（AKSK + canonical request + HMAC）
3. DashScope 双步 + 区域 base URL 切换（北京/新加坡/美西 credential 不通用）
4. OpenAI Sora multipart 上传（本地文件 base64 时）
5. MiniMax 两步下载（`file_id → /files/{file_id}` 获取 download_url）
6. Luma keyframes 联合类型（`type: image|generation`）
7. Vertex AI Veo（service account JSON + OAuth2 token 刷新 + LRO 双轮询）
8. Runway header version 校验
9. OpenAI Sora 内容下载分 variants（`?variant=video|thumbnail|spritesheet`）
10. PiAPI 状态码非标（外层字符串 + 内层 `output.status: 99` 整数）

**声明式 YAML 能覆盖的场景**正好是 ArcReel 已经在 V1 内置 backend 里覆盖的（NewAPI、OpenAI 兼容、Kie.ai 等纯 JSON in/out 流派），用户用声明式接入的动机被消除。

### 7.3 ArcReel 现状适配

ArcReel 当前**自定义供应商接入流程已经存在且不需要重新设计**：

1. 用户 → 设置页 → 添加自定义供应商
2. 填 `display_name` + `discovery_format` + `base_url` + `api_key`
3. 点 [获取模型列表] → 调 `/api/v1/custom-providers/discover`
4. UI 自动推断 endpoint
5. 用户勾选启用 / 手工调整 endpoint / 填价格
6. 保存到 `custom_provider` + `custom_provider_model` 表

**新增协议（如 Kling 官方、DashScope）的工程动作**：
1. 在 `lib/video_backends/` 新建 backend 类（实现 `VideoBackend` Protocol）
2. 在 `ENDPOINT_REGISTRY` 注册新 endpoint key 和 build_backend 闭包
3. 在 `infer_endpoint()` 加启发式规则
4. 在 i18n 文件加 `endpoint_xxx_display` 三语
5. 加 mock httpx 单测

**用户无需写代码**，无需修改任何配置文件，只需在前端 UI 添加自定义供应商时挂上对应 endpoint。

### 7.4 两种"新增 backend"场景的区分（前置概念）

需严格区分两种用户场景，它们的接入路径完全不同：

| 场景 | 描述 | 当前支持度 |
|---|---|---|
| **场景 A：协议已支持，接新中转站** | 用户找到一家新的 NewAPI / 流派 C 聚合站 / OpenAI 兼容站 | ✅ 纯 UI 操作，零代码（7.3 已述） |
| **场景 B：协议未支持，需新增协议适配** | 用户想接 Kling 官方（JWT）/ DashScope（特殊 header）/ 某个全新的私有中转协议 | ⚠️ 当前需改 `lib/video_backends/` + `ENDPOINT_REGISTRY` 源码 |

**场景 A 占绝大多数用户需求**，体验已经很顺畅。**场景 B 当前存在体验断层**——接新协议必须改源码、重新构建、走 PR 或本地 fork。运行时 plugin 功能正是为了消除场景 B 的断层、支持社区化协议分享。

---

## 7.5 运行时 Plugin 机制调研（功能规划支撑材料）

> 本节为 ArcReel 已规划的「运行时 plugin」功能提供可行性与设计选项的调研信息。目标：让用户无需修改 ArcReel 源码、无需重新构建镜像，即可加载第三方贡献的视频协议 backend，从而支持社区化分享、降低自定义协议接入门槛。本节**不替 PRD 做决策**，仅汇总可行路径、业界做法、与 ArcReel 现有架构的契合点和待解决问题。

### 7.5.1 ArcReel 现有注册机制现状（运行时 plugin 的改造起点）

调研 ArcReel 源码后确认的现状（这是设计运行时 plugin 必须基于的事实）：

1. **backend 注册是进程内静态字典**。三套 media backend（`video_backends` / `image_backends` / `text_backends`）各有一个 `registry.py`，模式完全一致：

   ```python
   _BACKEND_FACTORIES: dict[str, Callable[..., VideoBackend]] = {}

   def register_backend(name: str, factory: Callable[..., VideoBackend]) -> None:
       _BACKEND_FACTORIES[name] = factory

   def create_backend(name: str, **kwargs) -> VideoBackend:
       if name not in _BACKEND_FACTORIES:
           raise ValueError(f"Unknown video backend: {name}")
       return _BACKEND_FACTORIES[name](**kwargs)
   ```

2. **注册时机是模块 import**。`lib/video_backends/__init__.py` 在加载时显式 `register_backend(PROVIDER_GROK, GrokVideoBackend)` 等，把所有内置 backend 注册进字典。`register_backend()` 本身已经是公开 API，**运行时再调一次完全合法**——这是运行时 plugin 的天然切入点。

3. **协议归属在 `ENDPOINT_REGISTRY`（静态字典）**。`CustomProviderModel.endpoint` 字段只能取 `ENDPOINT_REGISTRY` 已注册的 key。这是当前最大的运行时扩展障碍：**用户即使运行时注册了新 backend，也无法注册新的 endpoint key 供模型挂载**。

4. **backend 构造参数从 DB 配置注入**。`create_text_backend_for_task()` 等工厂从 `ConfigResolver.provider_config()` 读 DB 取 `api_key` 等显式传入 backend 构造器，不再依赖环境变量 fallback（`2026-05-12-agent-sandbox-design` 已清理所有 env fallback）。运行时 plugin 的 backend 必须同样接受显式配置注入，不能读环境变量。

**结论**：ArcReel 的 `register_backend()` + Protocol 鸭子类型设计**天然适合运行时扩展**，核心改造点是让 `ENDPOINT_REGISTRY` 从静态字典变为「内置 + plugin 动态注入」两层结构，并补上 plugin 发现、加载、安全、生命周期管理。

### 7.5.2 业界运行时 plugin 机制对标

| 方案 | 发现机制 | 注册方式 | 适合 ArcReel 之处 | 不适合之处 |
|---|---|---|---|---|
| **Python entry_points**（PEP 621） | `importlib.metadata.entry_points(group=...)` | 用户 `pip install arcreel-plugin-xxx` 后自动可见 | 标准、零三方依赖、契合"社区分享 pip 包"愿景 | 必须打包成 wheel，本地裸文件不行；Docker 环境用户加包要重建镜像或挂载 |
| **目录扫描 + importlib** | 扫描指定目录下 `*_backend.py`，`importlib.util.spec_from_file_location` 动态加载 | 文件落地即生效，配合 `__subclasses__()` 自动捕获 | 自部署用户挂载 volume 即可加载，无需重建镜像；本地开发友好 | 需要自己处理重复加载、命名冲突、错误隔离 |
| **pluggy**（pytest 插件系统） | hook 规范 + setuptools entry_points | hook 多播（1:N） | 成熟稳定 | ArcReel 是 1:1 路由（一个 model → 一个 backend），不需要 hook 多播；pluggy 缺生命周期管理（token 缓存/连接池），属过度设计 |
| **LiteLLM `custom_provider_map`** | 配置文件中声明 `{provider, custom_handler}` | 模块路径 + 实例变量 | 直接对标"用户注册自定义 provider"场景 | LiteLLM 视频端点目前不支持 CustomLLM（连 embedding 都还在 issue 阶段），无法直接复用其视频路径 |

**业界共识**：entry_points（已发布插件）+ 目录扫描（本地开发/自部署）双轨制是当代 Python 应用插件发现的主流组合。ArcReel 的 Docker 部署形态决定了**目录扫描 + volume 挂载**对自部署用户更友好，而 entry_points 更适合"发布到 PyPI 供社区一键安装"的成熟插件。

**LiteLLM 的演进轨迹（直接参考价值）**：LiteLLM 最初只支持手工 `litellm.custom_provider_map = [{"provider": ..., "custom_handler": ...}]` 运行时赋值；社区在 issue #7733 提出希望用 entry_points 让第三方包自动注册（当时只能用 `.pth` hack），随后 PR #15881 实现了通过 `pyproject.toml` 的 `[project.entry-points.litellm]` 声明 CustomLLM 子类、由 `importlib.metadata` 自动发现注册。这条"手工注册 → entry_points 自动发现"的演进路径与 ArcReel 现状（`register_backend()` 手工调用）高度吻合,可作为分阶段实现的直接蓝本。

**一个必须规避的 LiteLLM 已知缺陷**：issue #23352 报告，当 plugin 注册的 model 名（剥离前缀后）与某个内置 provider 的已知 model 撞名时，请求会被**静默路由到内置 provider 而非 plugin handler**，且不报错。对 ArcReel 的启示：plugin endpoint 的派发优先级和命名空间隔离必须在设计时就明确，**显式注册的 plugin 应优先于启发式推断**，避免同名静默劫持。

### 7.5.3 运行时 Plugin 需要解决的设计问题（待 PRD 决策）

以下是从现有架构推导出的、运行时 plugin 必须回答的问题清单，作为 PRD 的输入：

**A. 发现与加载**
- plugin 来源：PyPI 包（entry_points）/ 本地目录（volume 挂载）/ 两者皆支持？
- 加载时机：进程启动时一次性加载，还是支持热加载（运行中新增 plugin 无需重启）？
- 加载失败隔离：单个 plugin 抛异常不能拖垮整个 backend registry，需要 try-except 包裹 + 降级日志（参考现状 `register_backend` 缺 key 不影响启动的设计哲学）。

**B. ENDPOINT_REGISTRY 的可扩展化（核心改造）**
- plugin 是否能注册新的 endpoint key？如果能，需要把 `ENDPOINT_REGISTRY` 从模块级静态字典改为支持运行时注入。
- 新 endpoint key 的命名空间隔离：是否给 plugin endpoint 加前缀（如 `plugin:kling-official`）防止与内置 key 冲突？
- `infer_endpoint()` 启发式如何容纳 plugin endpoint：plugin 是否能声明自己的启发式规则（model_id 匹配模式 → 自己的 endpoint）？

**C. plugin 契约（backend 接口约束）**
- plugin backend 必须实现 `VideoBackend` Protocol（`name` / `model` / `capabilities` / `generate()`），这点现有 Protocol 已就绪。
- plugin 是否需要声明元数据：支持的 model 列表、能力位图、必填凭证字段（`required_keys` / `secret_keys`，参考 `ProviderMeta`）、显示名称（i18n）？
- plugin 的配置注入：如何让 plugin backend 拿到用户在 UI 填的 api_key / base_url？需要复用现有 `CustomProvider` 的凭证存储 + `mask_secret()` 掩蔽。

**D. 安全（运行时执行第三方代码的核心风险）**
- plugin 是任意 Python 代码，运行在 ArcReel 进程内，拥有完整文件系统/网络访问权限。
- 是否需要沙箱？ArcReel 已有 agent sandbox（bubblewrap/seatbelt，见 `2026-05-12-agent-sandbox-design`），但那是针对 Agent SDK 的 Bash 子进程隔离，**backend plugin 运行在主进程**，无法直接复用同一沙箱。
- **进程内 Python 沙箱不可行（调研明确结论）**：RestrictedPython 官方自述"is not a sandbox system or a secured environment"，业界共识是 CPython 进程内沙箱因 Python 动态特性（`__import__` 滥用、introspection 逃逸、反序列化攻击）几乎无法做到真正安全（pysandbox 作者已宣告此路不通）。真正的隔离只能靠进程/容器边界（seccomp、namespace、micro-VM 如 Firecracker、WASM 编译），但这些都与"plugin 作为 backend 在主进程被调用"的形态冲突。**因此 ArcReel 的 plugin 安全不应寄望于代码级沙箱，而应走"来源信任"路线。**
- 缓解方向（来源信任路线）：plugin 来源审核（仅信任 PyPI 签名包 / 仅信任白名单 GitHub 仓库）、安装时人工确认、社区评分机制、官方维护的已审核 plugin 清单、安装前代码静态扫描（依赖/危险调用检测）——具体取舍待 PRD 决策。
- 凭证泄漏风险：恶意 plugin 可读取其他 provider 的 api_key。需要评估是否限制 plugin 只能访问挂载到自己的凭证。

**E. 生命周期管理**
- 连接池 / token 缓存：Kling JWT 30 分钟过期、连接池复用等有状态资源，plugin 实例的生命周期如何管理（单例 vs 每次请求新建）？现有 backend 通过 `(provider_name, model)` 缓存策略复用实例，plugin 应沿用。
- 卸载 / 更新：用户禁用或更新 plugin 时，如何清理已注册的 backend 和 endpoint？

**F. 分发与社区化**
- 社区分享形态：GitHub 仓库 / PyPI 包 / ArcReel 官方 plugin 市场？
- 版本兼容：plugin 声明兼容的 ArcReel 版本范围（Protocol 接口变更时的兼容策略）。
- 文档与脚手架：提供 `arcreel backend scaffold` 类工具 + AI prompt 模板，让用户用 Cursor/Claude Code 快速生成符合 Protocol 的 plugin 骨架（7.1 已论证 AI 辅助生成是 plugin 方案相比声明式的关键优势）。

### 7.5.4 与现有架构的契合度评估

| 改造点 | 现有基础 | 改造量 |
|---|---|---|
| backend 运行时注册 | `register_backend()` 已是公开 API，鸭子类型 Protocol 就绪 | 小（加 plugin 加载器调用即可） |
| ENDPOINT_REGISTRY 可扩展 | 当前静态字典 | **中**（需改为两层结构 + 命名空间隔离） |
| `infer_endpoint()` 容纳 plugin | 现有启发式硬编码 | 中（需开放 plugin 声明启发式规则的接口） |
| 凭证存储/掩蔽 | `CustomProvider` + `mask_secret()` 就绪 | 小（plugin 复用即可） |
| i18n endpoint 显示名 | `endpoint_xxx_display` 三语机制就绪 | 小（plugin 需提供自己的显示名，可能需 fallback 到英文） |
| 安全模型 | agent sandbox 不覆盖主进程 backend；进程内 Python 沙箱已证不可行 | **大/未知**（无法靠代码沙箱，只能走来源信任 + 审核机制,属产品/安全决策） |
| 生命周期/缓存 | `(provider, model)` 实例缓存就绪 | 小（plugin 沿用） |

**总评**：`register_backend()` + Protocol 的设计让运行时 plugin 的**功能实现**契合度高、改造量可控；真正的难点集中在两处——**ENDPOINT_REGISTRY 的动态化**（工程问题，中等）和**主进程执行第三方代码的安全模型**（产品 + 安全决策，需 PRD 重点论证）。由于代码级沙箱已被调研证明不可行，安全设计的重心应放在"来源信任 + 审核 + 社区治理"而非"技术隔离"。

### 7.5.5 可参考的渐进式落地思路（供 PRD 取舍，非定论）

调研发现的几种渐进路径，按改造从小到大排列：

1. **最小可用（仅自部署）**：目录扫描 + volume 挂载，仅支持自部署用户在受信环境加载自己写的 plugin，不解决安全问题（信任用户自己）。改造量最小，可快速验证机制。
2. **社区分享（PyPI + 审核）**：entry_points + PyPI 包发布，配合官方维护的"已审核 plugin 清单"，社区贡献需经过审核才进入推荐列表。平衡了开放性和安全性。
3. **官方 plugin 市场**：ArcReel 维护 plugin 注册表 + 版本兼容声明 + 社区评分，用户在 UI 内一键安装。体验最好，但需要市场基础设施和持续运营投入。

这三条路径不互斥，可作为功能演进的三个阶段。

---

## 8. 关键风险与坑点汇总

### 8.1 OpenAI Sora 2 Deprecation

- 官方发布通告 2026-03-24，**Sora 2 / Sora 2 Pro 及 Videos API 将于 2026-09-24 关停**
- 影响 model id：`sora-2`、`sora-2-pro`、`sora-2-2025-10-06`、`sora-2-2025-12-08`、`sora-2-pro-2025-10-06`
- ArcReel 必须在 2026-Q3 前完成 sora-3 或替代模型迁移评估
- `/v1/videos` 路径事实标准会保留，中转站惯性沿用

### 8.2 NewAPI metadata 透传完整度不可控

- 同一个 Kling `camera_control` 在 DMXAPI 能用，在某些自部署 NewAPI 上可能丢失
- ArcReel `newapi-video` backend 需要在 channel 配置或文档层面标注实测透传的字段集
- 部分高级能力（motion brush / camera control）建议在 UI 中标注"可能在某些中转站不可用"

### 8.3 视频 URL 过期统一陷阱

- 所有平台都用临时 URL，最危险的 MiniMax 仅 **9 小时**
- ArcReel 必须在 SUCCEEDED 后 **10 秒内启动转存**到本地或对象存储
- 不要依赖 vendor URL 作为前端展示链接

### 8.4 状态字符串差异

- Kling `succeed`（不是 `succeeded`）
- MiniMax `Success`（首字母大写）
- DashScope 全大写 `SUCCEEDED`
- PiAPI 内层 `output.status: 99`（整数）
- 各家 backend 必须自己维护状态映射表，不能复用

### 8.5 model id 命名碎片化

流派 C `/v2/video/generations` 同一模型在不同中转站命名完全不同：
- AIMLAPI：`kling-video/v1/standard/text-to-video`
- APIMart：`sora-2-vip`
- getimg.ai：`happyhorse-1`（自有品牌名）
- xAI 官方：`grok-imagine-video`

ArcReel 需要在 channel 配置层维护 model name 别名映射，或者在 `infer_endpoint()` 启发式中接受用户手工修正。

### 8.6 流派 C v2 的 discovery 局限

`/v1/models` 列表无法可靠区分 `/v1/video/generations` vs `/v2/video/generations` 的目标端点，因为两者 model id 命名完全一致。

**自动启发式只能给一个默认值**（建议 `newapi-video` 更常见），用户在 UI 上按实际中转站文档手工切换。这是 NewAPI / OneAPI 衍生中转站生态的固有限制，不是 ArcReel 的设计缺陷。

### 8.7 Seedance 2.0 国内/海外 model ID 不通用

- 国内：`doubao-seedance-2-0-260128`
- 海外：`dreamina-seedance-2-0-260128`
- 跨区调用必然 404
- ArcReel 已有 ark / ark-agent-plan 双 provider 设计可参考

### 8.8 内容审核错误码

各平台审核拒绝错误码完全不同：
- OpenAI Sora：`error.code: "moderation_blocked"`
- 阿里 DashScope：`IPInfringementSuspect` / `DataInspectionFailed`
- 可灵 Kling：HTTP 500 + `error: "...violate the community guidelines (CM_EXT.POther)"`（**用 error 字段而非 message**）

不应作为可重试错误处理，需要在 backend 层做特殊识别。

---

## 9. 后续 PRD / 设计文档需要解决的问题

本报告作为输入素材，后续 PRD 阶段需要明确以下问题。按"架构层 → 协议实现层 → 运行时 plugin 层"三组归类。

### 9.1 架构对齐评估（先于具体协议接入决策）

> 这一组是**比单个协议接入更高层的决策**：在大规模新增协议之前，先评估当前 `lib/video_backends/` + `lib/custom_provider/` 架构是否需要对齐业内成熟实现做调整。**重要前提：避免被命名带偏。**

**关于命名（先澄清，避免误导）**：
- ArcReel 当前的 `VideoBackend` Protocol + 各 `XxxVideoBackend` 实现，**本质上已经是业内推崇的 Ports & Adapters（六边形架构）范式**——Protocol 即 Port，各 backend 即 Adapter。架构骨架已对齐优秀实践。
- 术语表（CONTEXT.md）选择 `backend` 而非 `adapter` 命名，理由是：backend 与 provider 的"派生"语义契合（一个 provider 派生多个 backend）、与 frontend 对仗、以及 video/image/text 三套媒体后端命名一致。**SQLAlchemy / Django 等成熟项目同样用 backend 而非 adapter 命名同类角色**，命名本身不构成"未对齐业内"的问题。
- **结论倾向**：不建议以"对齐 adapter 命名"为目标做重构；命名是表层，真正该评估的是下面的能力缺口。

**真正需要 PRD 评估的架构缺口（与命名无关）**：

1. **统一异步任务抽象是否需要提取为一等公民**。本次调研确认：9 家官方平台 + 4 大中转流派**全部是异步任务模型**（submit → poll → 状态映射 → URL 转存），且状态机字符串、URL 过期窗口、两步取 URL 等各不相同。
   - **真信号判断点**：当前各 backend 是否在重复实现 poll 循环 / 状态映射 / URL 转存？若重复度高，应在 `VideoBackend` 之上抽出 `AsyncVideoTask` 类抽象（可参考 fal.ai queue 模型）。若已有共享 poll 基础设施，则不必动。
   - 这是该对齐业内的实质点，与命名无关。

2. **新增协议的耦合度审查**。理想状态：加一个新协议（如 Kling）只需新增一个 backend 类 + 在 `ENDPOINT_REGISTRY` 注册一行。
   - **真信号判断点**：若加新协议需同时改 `resolver` / `cost_calculator` / `media_generator` / 多个 enum，说明耦合过紧，需解耦——但解耦方案与命名无关。

3. **ENDPOINT_REGISTRY 静态字典的可扩展化**。这是运行时 plugin 的硬阻塞（详见 7.5.1），也是与业内可扩展注册机制（entry_points / pluggy）的主要差距。属于明确该对齐的点（具体见 9.3）。

**驱动原则**：让真实痛点（加协议改文件多、异步轮询代码重复、plugin 卡静态字典）驱动重构，而非让"业内都叫 adapter"这一观察驱动重构。如无上述实质痛点，当前架构保持即可，只做增量扩展。

### 9.2 协议实现层（P0 协议接入的具体问题）

1. **P0 协议适配的具体接入次序**（建议先 v2-video-generations 流派 C → Kling 官方 → DashScope → MiniMax）
2. **每个新增 endpoint 在 `ENDPOINT_REGISTRY` 的 key 命名**（如 `v2-video-generations` / `kling-official` / `dashscope-async` / `minimax-video`）
3. **`infer_endpoint()` 启发式扩展规则**（如何区分新增 endpoint 与现有 endpoint）
4. **视频 URL 转存中间件设计**（针对 MiniMax 9h、Sora 1h 等短期 URL）
5. **MiniMax File API 两步流程在 backend 内部如何封装**（对外暴露统一的 video_url）
6. **Kling JWT token 缓存策略**（asyncio.Lock + dict TTL）
7. **DashScope 区域配置**（base_url 与 api_key 绑定关系）
8. **NewAPI backend 的 metadata profile 设计**（按 vendor 维护字段映射表）
9. **统一异步任务状态机抽象**（是否在 VideoBackend 层引入还是各自实现；与 9.1 第 1 点联动决策）
10. **Sora 2 deprecation 应对**（迁移评估窗口与 fallback 策略）

### 9.3 运行时 Plugin 层

1. **运行时 plugin 的 ENDPOINT_REGISTRY 动态化方案**（静态字典 → 内置+plugin 两层结构；与 9.1 第 3 点同源）
2. **运行时 plugin 的安全模型**（代码级沙箱已证不可行，重心放在来源信任 + 审核机制）
3. **运行时 plugin 的发现与加载机制选型**（entry_points / 目录扫描 / 两者）
4. **运行时 plugin 的契约定义**（元数据声明、凭证注入、i18n、启发式规则）
5. **运行时 plugin 的渐进式落地阶段划分**（最小可用 → 社区分享 → 官方市场）

---

## 10. 参考资料

### 一手 API 文档

- OpenAI Sora 2: https://platform.openai.com/docs/guides/video-generation
- OpenAI Deprecations: https://developers.openai.com/api/docs/deprecations
- NewAPI 文档: https://doc.newapi.pro/api/generate-video/ 和 https://doc.newapi.pro/api/kling-jimeng/
- 可灵 Kling: https://app.klingai.com/cn/dev/document-api
- 火山 Ark Seedance: https://www.volcengine.com/docs/82379
- 阿里 DashScope: https://help.aliyun.com/zh/model-studio/text-to-video-api-reference
- MiniMax Hailuo: https://platform.minimax.io/docs/api-reference/video-generation-t2v
- AI/ML API: https://docs.aimlapi.com/api-references/video-models
- xAI Grok: https://docs.x.ai/developers/model-capabilities/video/generation
- Kie.ai: https://docs.kie.ai/market/
- PiAPI: https://piapi.ai/docs/
- Runway: https://docs.dev.runwayml.com/
- Luma: https://docs.lumalabs.ai/docs/video-generation
- PixVerse: https://docs.platform.pixverse.ai/

### ArcReel 现有设计文档（背景对齐）

- `docs/superpowers/specs/2026-03-16-video-service-layer-design.md` — VideoBackend Protocol
- `docs/superpowers/specs/2026-03-31-custom-provider-design.md` — 自定义供应商初版
- `docs/superpowers/specs/2026-04-15-newapi-custom-provider-design.md` — NewAPI 接入
- `docs/superpowers/specs/2026-04-26-custom-provider-model-endpoint-design.md` — endpoint 下沉到模型层
- `docs/superpowers/specs/2026-05-04-video-duration-redesign-design.md` — duration 真相源
- `CONTEXT.md` — 词汇表（backend 而非 adapter）

### 中转站文档（事实标准来源）

- AiHubMix: https://docs.aihubmix.com/en/api/Video-Gen
- APIMart: https://docs.apimart.ai/en/api-reference/videos/
- 七牛云 AI 推理: https://developer.qiniu.com/aitokenapi
- useapi.net Kling: https://useapi.net/docs/api-kling-v1/
- fal.ai: https://fal.ai/models/

---

**报告版本**：v1（最终调研版）
**对齐架构**：ArcReel `lib/video_backends/` + `lib/custom_provider/` + `ENDPOINT_REGISTRY`
**下一步**：基于本报告撰写具体 endpoint 接入的 PRD 和设计文档
