# ArcReel

AI 视频生成平台：将小说转化为短视频。本文件是领域术语表（ubiquitous language），只定义概念，不含实现细节。

## Language

### 供应商与后端

**provider（供应商）**：
一个媒体生成能力的提供方，由 provider id 标识（如 `gemini-aistudio`、`gemini-vertex`、`ark`、`custom-{id}`）。provider 是**身份**，不是连接对象。
_Avoid_: vendor、channel。

**backend（后端）**：
按某个 provider + model 构造出来的、真正调用其 API 的客户端对象。一个 provider 可派生出多个 backend。backend 是**构造物**，与 provider 身份是两件事——"选哪个 provider" 和 "造哪个 backend" 是两个独立决策。
_Avoid_: client（太泛）、adapter（另有架构含义）。

**内置 provider（built-in provider）**：
ArcReel 启动时在 `PROVIDER_REGISTRY` 静态注册的供应商（如 `gemini-aistudio` / `gemini-vertex` / `ark` / `openai` / `grok` / `vidu`）。用户填凭证 + 选 model 即可使用；凭证字段可按供应商定制（如 Vertex AI 用 service account JSON、Ark 用 AKSK、Kling 用 JWT access+secret）。
_Avoid_: preset（易与 model preset 混淆）、official（误读为"获 vendor 官方授权"）。

**自定义 provider（custom provider）**：
用户运行时通过 UI 创建的供应商，`provider_id` 形如 `custom-{id}`。挂接一个 endpoint 决定协议形态；凭证模型固定为 `api_key`（单字段）+ `base_url`。主要承载中转站接入场景。需要多字段凭证（如 service account JSON、AKSK、JWT access+secret）的协议**无法**作为自定义 provider 接入，只能走内置 provider。

**endpoint（协议端口）**：
自定义 provider 可挂接的一种协议形态——HTTP URL 模板 + 鉴权约定 + 字段语义构成的"协议槽位"（如 `openai-video` 对应 OpenAI Sora `/v1/videos` 协议、`newapi-video` 对应 NewAPI 自有 `/v1/video/generations` 协议）。一个 endpoint 决定 backend 如何被构造和调用；endpoint 是协议归属的单一真相源，登记在 `ENDPOINT_REGISTRY`。一个内置 backend 可被同时用于内置 provider 和 endpoint 闭包，代码共享。
_Avoid_: protocol（太泛，易与 HTTP/JSON 协议混淆）、format（易与 image format / 文件格式混淆）、端口（含义重叠 network port，避免）。

**规范 provider id（canonical provider id）**：
`PROVIDER_REGISTRY` 的 key 形式，是 provider 身份的唯一真相源与全系统唯一接受的写入形式。
_Avoid_: legacy provider 名。

**legacy provider 名**：
旧版本写入 `project.json` 的非规范别名（如 `gemini`、`aistudio`、`vertex`、`seedance`）。属于待清除的历史数据，**不是**有效身份；经一次性迁移转为规范 id 后即不再被接受（见 `docs/adr/0001`）。

### 任务与取消

**task（任务）**：
GenerationQueue 中的一条记录，承载一次媒体生成请求。状态机：`queued → running → succeeded | failed | cancelling → cancelled`。
_Avoid_: job（无此概念）。

**cancelling（取消中）**：
中间状态，表示 cancel 信号已发出但 worker 内 asyncio task 尚未走完 finally 收尾。cancel API 把 DB 从 `running` 改成 `cancelling` 后立即返回；worker finally 在 mark 终态时只能从 `cancelling` 转 `cancelled`（不再走 succeeded/failed 分支）。这是状态机里唯一一个**从 `running` 出发、由 worker 之外的代码改写的非终态**——`queued` 由 enqueue API 写、`cancelled` 直接由 cancel queued 路径写都属于「外部写入」，但前者不从 running 出发、后者是终态。

**slot（执行槽）**：
GenerationWorker 内并发执行 task 的容量，维度是 **provider × media_type**（不是简单的 image/video 两条总通道）。每个 provider 各有独立的 image / video pool，默认容量分别为 `IMAGE_MAX_WORKERS=5` / `VIDEO_MAX_WORKERS=3`，可在 provider config 里覆盖；TTS 落地后并列新增 audio pool（`AUDIO_MAX_WORKERS`，默认值随实现设定——TTS 便宜快、倾向放宽，见 `docs/adr/0010`）。一个 provider 的 video 池满，**只阻塞该 provider 的 video 任务**，不影响其他 provider；但若用户的项目只配了一个 video provider，这等于阻塞所有 video 任务。
_Avoid_: concurrency limit（太泛）。

**ProviderPool**：
worker 内承载 slot 的数据结构（`lib/generation_worker.py:ProviderPool`），字段 `image_max` / `video_max` + 两个 `inflight: dict[task_id, asyncio.Task]`（TTS 落地后并列新增 `audio_max` + `audio_inflight`，见 `docs/adr/0010`）。inflight 字典是**worker 内存状态**，与 DB 中的 `status='running'` 必须配对维护——cancel 触发时由 worker 在 in-process task 字典里查到对应 asyncio.Task 后 `cancel()`，finally 收尾时从 inflight 移除并把 DB 从 `cancelling` 转 `cancelled`（见 `docs/adr/0006`）。`docs/adr/0006` 落地前 inflight 会出现「DB 改 cancelled 但 asyncio.Task 没被中断、名额仍被占」的撕裂，是已知遗留缺陷。

**worker（GenerationWorker）**：
ArcReel 中始终与 server 主进程**捆绑在同一个 uvicorn 进程内**的 background asyncio task，**不是**独立进程，**不是**集群成员。代码里的 `lease` / `heartbeat` / `requeue_running` 是早期遗留的"多 worker 协调"脚手架，从未被多进程使用。涉及 worker 的设计按"单进程 in-process 协调"思路。

**孤儿任务（orphan task）**：
DB 中状态为 `running` 但 worker 内存里没有对应 asyncio.Task 的任务。唯一现实成因是**服务重启**（部署 / 崩溃恢复）。处理原则：**不重新触发生成**（避免重复扣费），有 `provider_job_id` 的提交-轮询型任务理论上可恢复轮询，否则标 failed。

**cancel（取消）**：
用户主动停止一个 task 的**日常路径**，要求秒级响应——不是只改 DB 状态等下次检查点，而是真正中断 worker 内对应的 asyncio task 并立即释放 slot。对 `queued` 和 `running` 都开放。
_Avoid_: abort（含义混淆，可能指系统侧失败）、stop（不区分主动/被动）。

**cancelled_by**：
取消来源标记。`user` 表示用户从 UI 触发；`cascade` 表示某个被取消任务的下游依赖一并被取消。系统内部超时回收**不**算 cancel（见 hang 与 timeout）。

### 解析

**provider 解析（resolve）**：
给定一个生成任务，决定它应使用哪个 **ProviderModel**。优先级自高而低：本次请求（payload）> 项目级（project.json）> 全局默认。这是"选身份"，不含 backend 构造。
_Avoid_: 用 "resolution" 指代此过程——`resolution` 专指图像/视频分辨率（见「尺寸与比例」），二义会混淆。

**ProviderModel**：
provider 解析的结果——一对 `(provider_id, model_id)`（provider_id 为规范 id）。是"选了哪个 provider 及其 model"的值对象，**不是** backend（未构造任何客户端）。
_Avoid_: ResolvedBackend、BackendSelection（会与 backend 混淆）。

**capability（t2i / i2i）**：
图片任务的两种形态——t2i 文生图（无参考图）、i2i 图生图（带参考图）。一个镜头属于哪种，取决于"开画那一刻"是否拼出了参考图，**只有执行时才能确定**（见 `docs/adr/0001`）；入队与调度（worker claim）这两个执行前环节都无法获知。视频任务无 capability 维度。

### 尺寸与比例

**比例（aspect_ratio）**：
输出的宽高比（如 `9:16` / `16:9` / `1:1`），项目级设定。是**输出比例的唯一真相源、永远优先**——比例错的分镜图/视频不可用。
_Avoid_: 把比例混进分辨率或尺寸字段。

**分辨率（resolution）**：
清晰度档位，**只决定清晰度规模，不决定比例**。图片档位 `512px`/`1K`/`2K`/`4K`，视频档位 `480p`/`720p`/`1080p`/`4K`，也可为自定义值。自定义值若自带比例（如 `1920x1080`），只取其**短边**作清晰度规模、剥离其比例——比例仍由 aspect_ratio 决定。缺分辨率但必需尺寸来控制比例时，兜底默认 720P（见 `docs/adr/0011`）。
_Avoid_: 用 resolution 指代 provider 解析（见「provider 解析」）；让分辨率值携带的比例压过 aspect_ratio。

**尺寸（size）**：
最终下传给后端的 宽×高 像素，由 **比例 × 分辨率档位** 在各后端像素约束内推导（统一机制见 `lib/aspect_size.py`）。接受任意像素的后端零比例偏差；档位受限的后端（如 sora-2 固定枚举、ark 像素预算下限）在约束内取比例最接近档，偏差作固有例外。
_Avoid_: 把 size 当比例或清晰度的同义词——它是二者派生的结果。

### 参考图与压缩

**参考图（reference image）**：
喂给 I2I / I2V / R2V 作为**条件输入（conditioning）**的图，提供身份/风格/构图引导。是模型生成的**输入**，与模型**产出**是两回事。一次生成可带多张（角色/场景/道具 sheet + 额外参考图 + 上一张分镜图等）。
_Avoid_: 用「参考图」指代生成产出或源资产文件。

**参考上传副本（reference upload copy）**：
把参考图编码进供应商请求体那一刻所用的**那份字节数据**。是临时副本（内存缓冲 / 临时文件），用完即删；不是磁盘上的源资产文件，也不是生成产出。三者必须分清：**源资产文件**（如 4K `character_sheet.png`，只读）、**生成产出**（模型返回的成品，全质量落盘，无保存时压缩）、**参考上传副本**（唯一会被压缩的对象）。
_Avoid_: 把「压缩参考图」误读为压缩源文件或产出。

**参考图压缩（reference image compression）**：
仅对**参考上传副本**做的等比缩放 + 重编码，目的是在不超出供应商请求体大小上限的前提下、尽量不损伤条件效果。因其只动发完即删的副本，对源资产与产出**零影响**——「生成 4K 却拿不到 4K」在此机制下不可能发生。决定压到多大属于「目标模型」决策，不属于本术语表。
_Avoid_: 把它与上传保存时压缩（`normalize_uploaded_image`，针对用户上传）混为一谈。

### 计费

**成本快照（cost snapshot）**：
一次 API 调用完成时（`ApiCall` 从 `pending` 转 `success`），由 `CostCalculator` 按**当时**的模型与计费参数算出金额，**冻结写入该调用记录的 `cost_amount` + `currency`**。所有用量与费用聚合一律 `SUM(cost_amount)` 读这个冻结值，**不在读时重算**。两条推论：① 调整定价只影响**之后**的新调用，不会追溯改变历史记录；② 下线模型的过往花费已锁定，定价数据无需为历史计费保留旧费率。
_Avoid_: 实时计费、读时重算成本。

### 媒体类型与配音（TTS）

**media_type / call_type**：
贯穿全系统的媒体维度，取值 `image` / `video` / `text` / `audio`，provider 解析、后端家族、用量与计费都"按 media_type 扇出"。同一个 token 必须在 `ModelInfo.media_type`、`CallType`、UsageTracker、CostCalculator、pricing 查询处保持一致。
_Avoid_: modality（太泛）、media kind。

**audio（媒体类型）**：
第 4 个 media_type，承载文本转语音（TTS）。与 image/video/text 平级，**经 GenerationQueue/Worker 调度**（像 image/video，不像同步内联的 text 生成）——因为旁白音频按 segment 一段、每集 N 段、可批量重生，其生成基数与 image/video 一致，而非 text 的"每集一次"。注意一个非对称：audio 的 **backend 调用本身是同步一次性**（仿 text_backends，秒回，无提交-轮询），但**任务编排仍走队列**（worker claim → 调同步 backend → 标终态），因此 audio 既进任务面板（进度/取消/续传），又不需要 video 那套 resume/`provider_job_id` 机制（见 `docs/adr/0010`）。
_Avoid_: tts（留给 capability）、voice、speech。

**text_to_speech（capability）**：
audio 媒体类型的能力标识，表示"把文本合成为语音"。在 audio 模型的 `ModelInfo.capabilities` 里声明，与图片的 t2i/i2i 同属 capability 维度。
_Avoid_: tts、voice_synthesis。

**旁白配音（narration voiceover / narration_audio）**：
对说书模式每个 NarrationSegment 的 `novel_text`（小说原文）生成的一段语音，是 audio 媒体类型在本期的唯一产物。按 segment 一段，落地为音频文件，路径记在该 segment 的 `GeneratedAssets.narration_audio`。
_Avoid_: dub（易与影视译制混淆）、TTS 音频（太泛）。

**"audio" 的三种含义（歧义警示）**：
- **audio（媒体类型）** = 本表定义的 TTS 维度。
- **`generate_audio`（能力/字段）** = 视频模型（Veo/Kling 等）**自带音轨**的开关，属 video 维度，与 TTS 无关。
- **`ambiance_audio`（脚本字段）** = 喂给视频模型的**环境音效提示词**，是文本而非音频文件。
新增 TTS 相关命名一律避开 `generate_audio` / `ambiance_audio` / `resolution_audio`（Veo 视频计费维度），防止与 audio 媒体类型混淆。

## 示例对话

> **Dev**：worker 认领一个图片任务时，怎么知道用哪个 provider 限流？
> **Expert**：它做 provider 解析，但只到"选身份"为止——拿 provider 不拿 backend，更不真正生成。
> **Dev**：那它知道是 t2i 还是 i2i 吗？要是用户给两者配了不同 provider？
> **Expert**：不知道。capability 执行时才定，worker 只能按 t2i 取个代表性 provider 限流。真正用哪个，执行层会重新精确解析一次。
> **Dev**：那 project.json 里要是写着 `seedance` 呢？
> **Expert**：那是 legacy provider 名，迁移后不该再出现。系统只认规范 id `ark`。
>
> **Dev**：旁白配音的 TTS 后端是同步一次性 POST，跟 text 生成一样不异步——那它也像 text 那样不入队、直接调？
> **Expert**：不。是否入队看**生成基数**，不看 backend 同不同步。text 每集生成一次，同步内联就够；旁白音频每 segment 一段、每集 N 段、要批量，基数和 image/video 一样，所以走队列、进任务面板（见 `docs/adr/0010`）。
> **Dev**：backend 同步又入队，不矛盾吗？
> **Expert**：不矛盾。worker claim 到 audio 任务后调那个同步 backend，秒回就标终态——只是省掉了 video 那套 submit-poll-resume。它占该 provider 的 audio pool，与 image/video pool 并列；TTS 便宜，`AUDIO_MAX_WORKERS` 默认放宽，一般不是瓶颈。
