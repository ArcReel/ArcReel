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
ArcReel 启动时在 `PROVIDER_REGISTRY` 静态注册的供应商（如 `gemini-aistudio` / `gemini-vertex` / `ark` / `openai` / `grok` / `vidu`）。用户填凭证 + 选 model 即可使用；凭证字段可按供应商定制（如 Vertex AI 用 service account JSON 文件路径、Kling 用 JWT access_key + secret_key）。
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

**registry 键 ↔ `api_model_name`（API 模型名）**：
`PROVIDER_REGISTRY[provider].models` 的键（model_id 字符串）是模型的**内部唯一标识**，兼 UI / 持久化标识与计费、能力查表键，是全系统唯一接受的模型写入形式。`ModelInfo.api_model_name`（默认 `None`）是**实际发给供应商 API 的模型名**——仅当它需要与键名不同（两栖模型）时才填，`None` 时回退键名（见 `docs/adr/0038`）。
_Avoid_: 把 registry 键直接等同于发给供应商的模型名（两栖模型下会发错）。

**两栖模型（amphibious model）**：
同一个供应商 API 模型名同时承载图像与视频两种 media_type 的模型（如可灵 `kling-v3-omni`，出图与出视频在可灵 API 同名）。因 registry 键与 `ModelInfo.media_type` 均单值，两栖模型拆成两条 registry 条目：其中一种 media_type 用**别名键** + `api_model_name` 回指真实 API 名、另一种占主键；哪种占主键是各模型的工程选择、非硬性规则（可灵 v3-omni 的选择是图像用别名键 `kling-v3-omni-image`、视频占主键 `kling-v3-omni`，见 `docs/adr/0038`）。
_Avoid_: 把别名键当成真实模型名；为两栖单独给 registry 键上复合 `(model_id, media_type)`（ADR 0038 已否决）。

**discovery_format**：
自定义 provider 的 provider 级字段（取值 `openai` / `google`），只决定「模型发现」与「连通测试」去查哪套列表 API；**不决定任何模型的调用协议**——调用协议由每个模型各自挂的 endpoint 决定。
_Avoid_: api_format（旧名，连同 `newapi` 取值已删除；它暗示「一个 provider = 一种协议」的错误读法）；把它当模型调用协议开关。（发现 API 另兼容 `anthropic` 探测，但不落库、不参与协议派发。）

**活跃凭证（active credential）**：
同一供应商（或 Agent Anthropic 配置）下配置多套凭证时当前生效的那一套，由用户在 UI 手动切换、全局生效，每个供应商至多一条活跃凭证；删除活跃凭证时，供应商凭证自动改选最早创建的另一条，Agent 凭证则不可直接删除、必须先切换（见 `docs/adr/0016`）。
_Avoid_: default credential（与「默认 model / 默认 backend」混淆）；把切换理解为自动轮换或负载均衡——系统只手动切换。

**Agent 凭证（agent credential / Anthropic 凭证）**：
供 Claude Agent SDK 使用的 Anthropic 兼容网关凭证（base_url + api_key + routing model），存于独立的 agent 凭证表，与自定义 provider 凭证是**两套互不相通的存储**（见 `docs/adr/0017`）。
_Avoid_: 把它当成一个自定义 provider（`custom-{id}`）——agent 凭证不进 `ENDPOINT_REGISTRY`、不参与媒体生成；自定义 provider 也不会注入 Agent SDK。

### 任务与取消

**task（任务）**：
GenerationQueue 中的一条记录，承载一次媒体生成请求。状态机：`queued → running → succeeded | failed | cancelling → cancelled`。
_Avoid_: job（无此概念）。

**cancelling（取消中）**：
中间状态，表示 cancel 信号已发出但 worker 内 asyncio task 尚未走完 finally 收尾。cancel API 把 DB 从 `running` 改成 `cancelling` 后立即返回；worker finally 在 mark 终态时只能从 `cancelling` 转 `cancelled`（不再走 succeeded/failed 分支）。这是状态机里唯一一个**从 `running` 出发、由 worker 之外的代码改写的非终态**——`queued` 由 enqueue API 写、`cancelled` 直接由 cancel queued 路径写都属于「外部写入」，但前者不从 running 出发、后者是终态。

**slot（执行槽）**：
GenerationWorker 内并发执行 task 的容量，维度是 **provider × media_type**（不是简单的 image/video 两条总通道）。slot 拆成两件性质不同的东西：**容量**是 provider config 给的上限标量（唯一真相，用户改设置才变），默认 `IMAGE_MAX_WORKERS=5` / `VIDEO_MAX_WORKERS=3`，可在 provider config 里覆盖；每条 lane 按三层回退取值——**用户配置值 > 供应商在注册表（`ProviderMeta.default_concurrency`）声明的出厂默认 > 全局默认**，声明默认是给上游容量受限的供应商出厂即串行/限并发的中间层，未声明的供应商仍退全局默认；**占用**是 worker 内存里在跑 / 排队的 task 记账（随 task 来去一直在变）。TTS 落地后并列新增 audio 容量（`AUDIO_MAX_WORKERS`，默认值随实现设定——TTS 便宜快、倾向放宽，见 `docs/adr/0010`）。一个 provider 的 video 池满，**只阻塞该 provider 的 video 任务**，不影响其他 provider；但若用户的项目只配了一个 video provider，这等于阻塞所有 video 任务。用户可配的并发上限是 **≥1 的整数，或留空（= 回退默认）**；`0` 不是合法用户输入，仅作 CapacityTable 内部「不支持该 lane」哨兵（由 `_lane_limits` 按 `media_types` 投影产生），见 `docs/adr/0043`。
_Avoid_: concurrency limit（太泛）。

**CapacityTable / SlotTable**：
worker 内承载 slot 的两个独立数据结构（`lib/generation_worker.py`），把容量与占用彻底分开。
- **CapacityTable** —— 纯标量上限表（`provider_id × media_type → 上限`）。provider config 是唯一真相，reload 只换表上的数字（`replace`），占用台账不受影响。`get` 三态语义：已知 + lane 在表→登记值（`0`=不支持该 lane）、已知缺 lane→`0`、provider 未知→懒默认（纯查询不写回）。
- **SlotTable** —— 被动纯内存占用台账（`(provider_id, media_type) → {task_id: 占用}`）。记 inflight + pending（video sem 排队期的瞬态用 phase 标志区分，promote 只翻标志）；职责限于：判有无空位（容量由 caller 传入，结构本身容量无关）、按 task 找执行体（cancel）、报告完成（worker 记账）。**不写 DB、不解析 provider、不决定孤儿策略、不碰 `docs/adr/0006` 状态机守卫**。空 bucket 在最后一个占用释放时一并剪除（池满黑名单源 `occupied_providers` 的正确性支点）。

占用台账是 **worker 内存状态**，与 DB 中的 `status='running'` 必须配对维护——cancel 触发时 worker 经 `find_by_task` 找到 asyncio.Task 后 `cancel()`，finally 收尾时 `release` 并把 DB 从 `cancelling` 转 `cancelled`（见 `docs/adr/0006`）。两者都以 `media_type` 为键维度为 audio lane 铺路：SlotTable 已能按 `(provider, "audio")` 记账、CapacityTable 容量装载收口在 `_lane_limits` 一处；但真正接入 audio 还需把 claim 循环（当前硬编码 `("image","video")`）与 `_extract_provider` 的 provider 解析纳入 audio lane（本次有意未做，见 `docs/adr/0010`）。

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

**supported_durations**：
某视频模型允许的离散时长集合（秒），是该模型时长的单一真相源；连续区间也会按整数全部展开为离散集（第一方模型恒为非空）。剧本 prompt、前端选择器、视频请求体三处同源消费（见 `docs/adr/0018`）。
_Avoid_: `VALID_DURATIONS` / 全局时长白名单（已删除的硬编码 `[4,6,8]`，与 per-model 概念相反）；把它当各家「官方时长能力表」（自定义供应商侧只是启发式预填、需用户 review）。

**default_duration**：
项目级偏好时长（int）；为 null 或缺失时是一个有语义的「auto」档——由 AI 按内容节奏在 supported_durations 内自行决定，**不是**「未设置 / 待填」。
_Avoid_: 把 null 读成「未配置」而擅自补默认值；与分镜级逐个时长选择混为一谈。

**「不传」语义（resolution = None）**：
分辨率作为**纯清晰度**且 SDK 非必传时，未配置即解析为 None——含义是「调用 SDK 时不携带该参数」、走 SDK 自身默认，而非我方填兜底默认值；`DEFAULT_VIDEO_RESOLUTION` 等我方默认表已删除（见 `docs/adr/0019`）。
_Avoid_: 把 None 当「用某个默认分辨率」而擅自填值。注意当尺寸须**承载比例**时不适用——该场景由 `aspect_size` 始终计算并下传（见 `docs/adr/0011` 与「尺寸」「分辨率」条）。

### 参考图与压缩

**参考图（reference image）**：
喂给 I2I / I2V / R2V 作为**条件输入（conditioning）**的图，提供身份/风格/构图引导。是模型生成的**输入**，与模型**产出**是两回事。一次生成可带多张（角色/场景/道具 sheet + 额外参考图 + 上一张分镜图等）。
_Avoid_: 用「参考图」指代生成产出或源资产文件。

**参考上传副本（reference upload copy）**：
把参考图编码进供应商请求体那一刻所用的**那份字节数据**。是临时副本（内存缓冲 / 临时文件），用完即删；不是磁盘上的源资产文件，也不是生成产出。三者必须分清：**源资产文件**（如 4K `character_sheet.png`，只读）、**生成产出**（模型返回的成品，全质量落盘，无保存时压缩）、**参考上传副本**（唯一会被压缩的对象）。
_Avoid_: 把「压缩参考图」误读为压缩源文件或产出。

**参考图压缩（reference image compression）**：
仅对**参考上传副本**做的等比缩放 + 重编码，目的是在不超出供应商请求体大小上限的前提下、尽量不损伤条件效果。因其只动发完即删的副本，对源资产与产出**零影响**——「生成 4K 却拿不到 4K」在此机制下不可能发生。决定压到多大属于「目标模型」决策，不属于本术语表（见 `docs/adr/0012`）。
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

### 项目与资产

**设计图（sheet）**：
AI 生成的角色/场景/道具定型图（`character_sheet` / `scene_sheet` / `prop_sheet`），是资产生成阶段的**产出**，随后作为 reference image 输入下游分镜/宫格/参考生视频以锚定一致性。
_Avoid_: 与「参考图（reference image，生成的条件输入）」混为一谈——方向相反：sheet 是产出后再被引用，参考图是输入；也不要与 character 的用户上传 `reference_image` 字段混淆（那是用户上传的参考文件，非 AI 定型图）。

**全局资产库（global asset library）**：
跨项目复用 character/scene/prop 三类资产的全局单一仓库（DB 持久化 + `_global_assets/` 图片目录），与项目以**快照复制**而非引用关联。
_Avoid_: 把它与项目当「引用耦合」——入库 / 应用到项目都物理复制图片，改一边不影响另一边；以为改名/删除库内资产会传导到已用项目；把 product 放进来——多图列表型资产不兼容库的单图列模型，spec 以 `in_global_library=False` 豁免。

**产品资产（product）**：
第 4 个 ASSET_SPECS 条目（bucket `products`、sheet 字段 `product_sheet`、子目录 `products/`），承载广告/短片项目的带货主体。持有列表字段 `reference_images`（用户上传多张原图，保存时保留原件不压缩，是「成片产品忠实于真品」的**保真验收锚点**）与 `selling_points`（卖点列表，agent 可起草、用户可改），及自由文本 `brand`。product sheet 是可选的标准化多角度派生参考（生成时原图全量注入），须经人工确认才进下游（agent 工作流软门禁，不设状态机）。下游注入二元：镜头 `products_in_shot` 非空即产品镜头——产品参考全量注入、排在所有其它参考之前并附高保真还原指令（有 sheet 时「sheet 多角度 + 原图压阵」，无 sheet 时原图直注），视频层按后端 reference 能力门控二次注入、不支持的后端正常降级；氛围镜头零产品图（见 `docs/adr/0034`）。
_Avoid_: 把 `reference_images` 交给 agent 改写——系统级字段不在 agent 白名单，更新走专用上传 API；把原图与 sheet 的锚点地位颠倒——原图必有且永远是验收基准，sheet 只是净化派生；对原图套用 2MB/q85 保存压缩——那是其它资产上传的归一化策略，对锚点过狠；发明「弱注入」中间档——给图又求别太像机制上自相矛盾，画风统一由项目级 style 承载。

**风格模版（style template）**：
预置的整段画风 prompt 文本（真人 / 动画两类，按 id 选一）。选定时把展开后的 prompt 写入 project.json 的 `style` 字段（供注入用的快照），同时保留 `style_template_id`（可在 PATCH / 读时迁移被重新解析）；registry 改动不主动回写老项目（见 `docs/adr/0023`）。
_Avoid_: 把 style 理解为短标签（旧值 Photographic/Anime/3D 已废，仅作 legacy 别名懒迁移）；与风格参考图（`style_image`，用户上传的画风参考）叠加——二者互斥，写入一方即清除另一方。

**线索（clue）— legacy 资产术语**：
ArcReel 早期对「场景 + 道具」的统称（按 type 区分 location/prop）；现已拆为独立的 scene 与 prop 两类资产，clue 及其 `importance` 字段不再是当前数据模型的概念。
_Avoid_: 在新代码/文档里用 clue/线索 指代场景或道具——规范词是 scene 与 prop；仅在读历史 project.json、迁移代码与归档设计稿时会遇到 clue。

### 剧本与分镜

**骨架（skeleton / 骨架种类 skeleton kind）**：
剧本条目数组的结构种类，四值：`segments`（说书片段）/ `scenes`（剧集场景）/ `shots`（广告镜头）/ `video_units`（参考生视频单元）。骨架是由 content_mode 与 generation_mode 两轴**派生**的概念，本身不是第三条轴：narration/drama/ad 各对应前三种骨架；narration/drama 在 generation_mode=reference_video 下整体换用 video_units 骨架，ad 骨架恒为 shots、不随生成路径变（见 `docs/adr/0033`）。对骨架有两种合法提问——**规范性**（按项目/剧集的模式配置，这份剧本*应该*是什么骨架）与**取证性**（这份剧本数据*实际*是什么骨架）；两者在部分迁移等中间态下可能不一致，取证以数据形状优先。骨架知识收归零依赖叶子模块 `lib/script_skeleton.py`：以骨架种类为键的窄表 `SKELETONS`（键即条目数组键，行 `Skeleton(id_field, chars_field)`，`video_units` 无逐条角色名单故 `chars_field=None`）+ **规范解析** `resolve_declared_kind(content_mode, generation_mode)`（服务手持项目配置的消费方，未知/缺失 content_mode 抛 `ValueError`）+ **取证解析** `resolve_script_kind(script)`（服务手持剧本数据的消费方，保留数据形状优先的容忍阶梯）；两个解析器是全体消费方分派骨架的单一入口，设计依据见 `docs/adr/0045`。
_Avoid_: 把骨架当第四个 content_mode 或 content_mode 的同义词（三值轴推不出四种骨架）；把规范性与取证性两问混同（配置已改 reference_video 但数据仍在 segments 时，编辑要跟数据走）；对未知模式做「非 narration 即 drama」式二值兜底（`docs/adr/0033` 禁令）。

**宫格（grid）**：
把同一段落多个场景合并成一张 N 格联合大图一次生成（grid_4/6/9）、再切割成各场景首尾帧的分镜生成路径；与逐张图生视频（storyboard）同为 generation_mode 下的「分镜→视频」路径，核心价值在一次生成保证画风/角色一致。
_Avoid_: 把 reference_video 当作与 grid/storyboard 同维度的第三个平级取值——它跳过分镜、是凌驾于 content_mode 之上的独立骨架，并非这种「分镜→视频」路径；逐张模式的规范值是 storyboard，而非旧用语 single。

**广告/短片模式（ad）**：
content_mode 第三值，产出单个约 `target_duration` 秒的短视频而非多集系列。剧本骨架为平铺 `shots[]`（`shot_id` 格式 E1S{n}），每镜头携带 `section`（带货框架段落标签，八值引导不硬枚举）与一等口播文案 `voiceover_text`；项目恒单集（episodes 恒为第 1 集单条），项目级新字段 `target_duration`（正整数秒）与 `brief`（创作诉求短文本，不走 source_loader），不持有 `default_duration`；generation_mode 仅开放 storyboard 与 reference_video（见 `docs/adr/0033`）。剧本一键生成不走 step1 中间文件：prompt 直接来自 brief + 产品信息（含 selling_points）+ 审定的带货八段框架配比表（15/30/60/90 取最近档位，依据见 `docs/research/arcreel-ad-section-timing-research.md`），products 为空自动分流通用短片 prompt；镜头时长约束随生成路径切换——storyboard 为 supported_durations 硬枚举、reference_video 为 1-15 秒自由整数；剧本总时长偏离 `target_duration` 超阈值仅 warn 不阻塞。
_Avoid_: 让 ad 落入「非 narration 即 drama」的二值兜底——所有按 content_mode 分派的机制必须显式处理第三值；把 AdShot 与 video_unit 内的 shot（参考生视频子镜头）混为一谈——前者是剧本骨架的平铺镜头、后者是 unit 内时间编排；把 ad 未接入 step1→step2 审核 gate 当作待补缺口——单发生成、无 step1 中间态是有意契约，重访条件见 `.out-of-scope/ad-step1-step2-review-gate.md`。

**video_unit / shot（参考生视频单元）**：
参考生视频模式下的生成单元：一个 video_unit 含 1–4 个 shot（子镜头），整 unit 共享一组按顺序编号的参考图（`[图N]`），跳过分镜直接由资产图生成。narration/drama 下剧本用 `video_units[]` 而非 `segments[]` / `scenes[]` 组织（unit 内容自包含）；ad 下骨架不变，unit 是从 `shots[]` **派生分组**的轻量索引（剧本 `reference_units[]`，仅引用 shot_id + 继承的参考集，产品参考绝对优先）——连续镜头、每 unit ≤4 shot、总长受供应商时长上限约束，分组为纯函数（`lib/reference_video/ad_units.py`）、可复现，成员与参考集未变的 unit 重派生时保留产物。
_Avoid_: 把 shot 与 segment（说书片段）/ DramaScene（剧集场景）混为一谈；「scene」在参考模式下三义须分辨——场景资产（scene_sheet）、剧本分镜场景（DramaScene）、镜头（shot）；手工增删改 ad 的 reference_units——它是派生物，shots 才是内容唯一真相。

**发声条目（utterance）**：
drama 场景里「说出来的话」的统一单元——每条要么是角色台词（有说话人），要么是画外音 / 旁白（无说话人）。一个 `DramaScene` 持有一条**有序**发声序列（`utterances`），插入顺序即幕内先后（台词与画外音交错的先后由此表达）。类型决定下游去向：台词进视频生成、由供应商生成口型音轨；画外音不进视频，留给成片字幕与日后 TTS。drama 的口播内容以此为单一真相源；narration 的口播不走 utterances，仍是被朗读的 `novel_text`。
_Avoid_: 把台词与画外音当两个独立无序字段（先后会丢、下游要拼两源）；把 utterance 与说书 `novel_text` 混为一谈——后者是整段被朗读的原文（基数为一）、前者是场景内逐条发声（基数为多）；把画外音塞给视频供应商音轨——供应商音轨只承载口型台词，画外音走字幕 / TTS。

**场景原文锚（source_text）**：
drama 场景级的逐字原文摘录——记录该场景所源自的原文片段，供人工审阅对照、单场景重生成与失真定位。是 best-effort 追溯锚（由 LLM 复制、可能轻微漂移），不是逐字保真的 ground truth（保真由提取流水线保证）；本身不被朗读、不出音，与作为口播的发声条目分属两事。角色上类比说书 `novel_text`，但 `novel_text` 身兼原文与被朗读的口播，`source_text` 纯作原文锚。
_Avoid_: 把 source_text 当会被配音 / 朗读的内容；与 episode 级 `source_range`（集对应的原文偏移区间）混为一类——一个是场景级逐字文本、一个是集级字符偏移。

**源文件性质（source_kind）/ 剧本源（screenplay source）**：
project.json 顶层字段，取值 `novel`（小说，默认——现状行为）/ `screenplay`（用户上传的成品剧本）。标记源文件**已是作者写好的成品剧本**而非待改编的小说。`screenplay` 时整条 drama 链路从「创作」翻为「提取优先」：分集边界、场景、台词、集尾钩子按剧本**原样提取**（作者即权威），LLM 只补剧本未写的视觉生产层（image_prompt / video_prompt）。是与 content_mode（narration/drama/ad）/ generation_mode 都正交的第三条轴——「源文件性质」，不是内容类型也不是视频来源。
**逐字保真只锚「可听见的内容」**——角色台词文字与画外音文字（`DramaScene.utterances` 内的发声条目，台词带说话人、画外音无说话人）不改写、不丢、不润色；排版/标签（`△`/`【画外音】`/markdown）、运镜与舞台提示（`（航拍，全景）`/`（压低声音）`）、视觉描述、泛指群演（`老人甲`/空镜）一律由 LLM 裁量转写或剥离，泛指 speaker 不进资产（见 `docs/adr/0036`）。
_Avoid_: 用「剧本」同时指上传源与生成产物——上传源是「剧本源（screenplay）」、产物是「剧本（script JSON）」，两个概念；把 screenplay 当新 content_mode；对 screenplay 仍跑「改编式 step1」或「重规划式 plan_episodes」——那正是要消除的二次改写（台词丢失、作者分集被篡改）；把「逐字」理解为连排版/舞台提示/群演都原样照搬——逐字只约束「说出来的话」，不约束「看见的制作」与「纸面排版」。

**分集账本（episode ledger）**：
project.json `episodes[]` 即分集单一真相源：条目在 episode/title/script_file 之外扩展 `source_range`（原文素材范围）、`hook`（集尾钩子）、`outline`（drama 分集大纲）与 `ledger_status`（消费状态）；物理 `source/episode_N.txt` 是派生物（见 `docs/adr/0031`）。账本字段全部可缺失——缺失即旧式条目，由可重跑的回填（`lib/episode_ledger.backfill_episode_ledger`）补账。
_Avoid_: 以物理集文件的存在性推断分集状态或集数（Glob 推断是被替代的旧模式）；把账本字段与 StatusCalculator 读时注入的统计字段混为一类——账本持久化在 project.json，统计字段不落盘。

**ledger_status（消费状态）**：
账本条目的四态生命周期：planned（已规划未消费）/ consumed（已有下游产物：step1 中间文件、剧本或媒体）/ stale（重排后失效，标记而非删除）/ unanchored（回填无法锚定：内容对不上源文，或集文件缺失/不可读；锁定不参与重排，下游消费不受影响——有物理集文件时该文件即其最终记录）。
_Avoid_: 与读时注入的 `status`（draft/in_production/completed）混为一谈——同一条目上两键并存、语义不同；把 unanchored 当失败（它是诚实降级，精确子串匹配不做模糊锚定）。

**归一化坐标系（normalized source coordinates）**：
source_range 与 planning_cursor 的字符偏移全部落在 `lib/episode_ledger.normalize_source_text`（Unicode NFC + 换行统一）的输出空间；按偏移切片源文前必须先对源文执行同一函数。
_Avoid_: 拿偏移直接切原始文件内容——NFD（macOS/越南语导入）或 CRLF 源文会错位。

**planning_cursor**：
project.json 顶层字段，下一批分集规划在源文中的起点（`{source_file, offset}`，null = 无规划进度），由规划工具在每次提交时前移。`source/_remaining.txt` 余文文件已废除：迁移回填仍读取其内容换算游标，规划工具首次提交时将其清理。
_Avoid_: 把 `_remaining.txt` 当进度真相源（损坏即不可恢复正是账本要消除的旧模式）；把非空 cursor 当绝对最新——重跑回填只补新集范围、不前移非空值，规划起点以账本锚定范围末尾与 cursor 的较后者为准。

**分集规划（plan / replan）**：
服务端分集规划能力（`lib/episode_planner.EpisodePlanner` + SDK 工具 `plan_episodes` / `replan_episodes`）：从 planning_cursor 起读一个源文窗口，调项目配置的文本模型一次规划窗口内所有剧情弧完整的集（标题/钩子/范围；drama 含分集大纲），schema 强约束 + 锚点存在/唯一/连续机械校验失败自动重试，同一把项目锁内写账本、派生集文件并清理残留。plan 接收可选常驻 `instructions`（用户分集偏好，如按章节对齐切分）：非空时以「必须全部落实」的强度注入规划 prompt、优先于默认剧情弧完整性；不持久化，规划分多批时须由 agent 逐批重复携带，缺省/空白则与今日纯剧情弧行为逐字一致。replan 按用户自由文本意见从 from_episode 起局部重排：范围跨多个源文件时按文件拆为多段独立重切（单集不跨文件，文件边界即集边界，集号跨段连续编号）；波及已消费集需显式确认（标 stale），全局性意见（每集体量）回写项目设置作为结构化全局意见、后续批次自动继承（与 plan 逐批携带、不持久化的 `instructions` 相对；见 `docs/adr/0032`）。
_Avoid_: 让主 agent 自行读原文选切分点（peek/split 脚本是被替代的旧模式）；窗口字数/每批集数硬编码到指令——它们是工具内部默认，`planning_window_chars` / `planning_max_episodes` 项目设置可覆盖。

### 智能体运行时

**SessionActor**：
每个 Claude 会话一个专属 asyncio task，串行化该会话对 `ClaudeSDKClient` 的所有协议调用（connect / query / 中断 / disconnect）；SDK 客户端并发调用不安全，actor 就是这条串行化边界（见 `docs/adr/0028`）。
_Avoid_: 与 ManagedSession（会话内存状态容器）混为一谈——actor 是执行通道、ManagedSession 是状态；直接调用 `client.disconnect()` / consumer_task 是已被替代的旧模式。

**agent 运行 profile（agent runtime profile）**：
智能体专属的运行态配置树（`agent_runtime_profile/`：系统 prompt 变体 + 业务 Skill/Subagent），与开发者本地 `.claude/` **物理分离**，运行时按 manifest 物化进各项目目录。
_Avoid_: 用「.claude」「CLAUDE.md」笼统指代——开发态 `.claude/` 与 agent profile 是两套；也不要称为 agent config（与 Anthropic 凭证的 agent_config 路由重名）。

**profile 物化（materialization）**：
把 agent profile 按 manifest + sha256 复制进每个项目目录的过程，只同步声明过且校验通过的文件，并按项目 content_mode 选 `CLAUDE.{narration,drama,ad}.md` 变体落盘为单一 `CLAUDE.md`。
_Avoid_: 用「同步 / 复制 / deploy」泛指——物化特指 manifest 驱动 + 变体投影 + sha256 三态的受控写入；变体源文件名（`CLAUDE.narration.md`）≠ 项目端逻辑文件名（`CLAUDE.md`）。

**agent 沙箱（agent sandbox）**：
Agent 工具调用外围的内核级隔离层（macOS Seatbelt / Linux bwrap），约束**沙箱内所有子进程**（Bash 及其派生进程）的文件读写与网络；SDK 内置 Read/Write/Edit/Glob/Grep 运行在主进程、不经过沙箱，由应用层 PreToolUse hook 拦截（见 `docs/adr/0025`、`docs/adr/0026`）。
_Avoid_: 用「沙箱」泛指应用层路径围栏 hook——沙箱专指内核级那一层；Windows 无内核沙箱，Bash 降级到前缀白名单。

**AgentAccessPolicy（agent 访问规则）**：
「agent 能碰什么」的单一规则真相源（`server/agent_runtime/agent_access_policy.py`）：以进程级根路径 + `sandbox_enabled` 纯构造、零 I/O，同一份规则做两种投影——为内核沙箱编译 SandboxSettings（denyRead/denyWrite/网络域名单），为应用层 hook 提供逐次读/写/命令裁决与 Bash 密钥剥离包装；Windows 降级（Bash 前缀白名单）收在类内，与「包装破坏白名单匹配」的互斥约束同处一地（见 `docs/adr/0046`）。SDK 封皮（hook 签名、权限结果类型、权限链顺序）留在 SessionManager 薄 adapter。
_Avoid_: SandboxPolicy——「agent 沙箱」专指内核级隔离层，本类同时服务不属于沙箱的应用层 hook；把凭证注入并入本类（注入读 DB，破坏纯构造）；在类内 import SDK 类型。

**SseChannel（订阅广播通道）**：
参数化的 SSE 订阅广播组件（`server/sse_channel.py`），会话消息流与项目事件流共用，职责限于订阅/退订、广播、空闲心跳、溢出处理，两处差异全部经参数表达：溢出策略（会话流「逐出非关键消息 + 溢出信号，流结束即重连信号」 vs 项目事件流「移除订阅者、无信号，断线靠心跳自检」）与可选的首/末订阅者生命周期钩子（项目事件流用于启停后台扫描）。开场白（会话流缓冲回放、项目事件流初始快照）不进组件，订阅与开场白的原子性由消费方在订阅侧的同步临界区保证（见 `docs/adr/0046`）。
_Avoid_: 把开场白生产塞进组件——缓冲回放与扫描快照无一行共同实现，参数化即假抽象；强行统一两种溢出语义；给已废弃的任务流端点（数据库轮询式）接入。

### 认证与凭证

**下载 token（download token）**：
项目导出专用的短时效（约 5 分钟）、绑定项目名的一次性 JWT（`purpose=download`），作为导出端点的 query param 唯一认证方式——端点自校验、不读 Authorization header，让浏览器原生下载的 URL 里不出现长效凭证。
_Avoid_: 与长效会话 JWT、API Key 混为一谈；把登录 JWT 放进下载 URL。

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
