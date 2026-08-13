# ArcReel

AI 视频创作平台：将小说、剧本或创作构想转化为短视频。本文件是领域术语表（ubiquitous language），只定义概念，不含实现细节。

## Language

### 供应商与模型

**供应商**：
向 ArcReel 提供文本、图片、视频或语音生成能力的外部服务方。
_Avoid_: provider、vendor、channel、后端。

**模型**：
供应商提供的一项具体生成服务；同一供应商可以提供多个用途和能力不同的模型。
_Avoid_: backend、把供应商与模型当作同一层概念。

**凭证**：
ArcReel 访问某个供应商所需的认证信息。
_Avoid_: 模型、供应商、连接。

**内置 provider（built-in provider）**：
ArcReel 启动时在 `PROVIDER_REGISTRY` 静态注册的供应商（如 `gemini-aistudio` / `gemini-vertex` / `ark` / `openai` / `grok` / `vidu`）。用户填凭证 + 选 model 即可使用；凭证字段可按供应商定制（如 Vertex AI 用 service account JSON 文件路径、Kling 用 JWT access_key + secret_key）。
_Avoid_: preset（易与 model preset 混淆）、official（误读为"获 vendor 官方授权"）。

**自定义 provider（custom provider）**：
用户运行时通过 UI 创建的供应商，`provider_id` 形如 `custom-{id}`。挂接一个 endpoint 决定协议形态；凭证模型固定为 `api_key`（单字段）+ `base_url`。主要承载中转站接入场景。需要多字段凭证（如 service account JSON、AKSK、JWT access+secret）的协议**无法**作为自定义 provider 接入，只能走内置 provider。

**接口类型**：
自定义供应商所采用的 API 契约，决定 ArcReel 如何组织请求、认证并理解响应。
_Avoid_: endpoint、协议端口、端口、接口格式。

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

**分层依赖方向（import layering）**：
层级自下而上为 `lib.config`（配置解析）→ `lib.*_backends`（供应商实现）→ `lib.custom_provider`（自定义供应商装配）；实际允许的 import 方向与此相反，即上层可以 import 下层（`custom_provider` → `backends` → `config`），下层反向 import 上层禁止。由 `pyproject.toml` 的 `[tool.importlinter]` 契约在 CI 强制，存量违规列在其 `ignore_imports`。
_Avoid_: 把「函数体内延迟导入」当作绕过方向约束的手段——linter 按静态语法计入，延迟导入同样是一条边。

### 任务与取消

**生成任务**：
ArcReel 为完成一次媒体生成而排队和跟踪的工作。生成任务从等待开始，最终完成、失败或被取消。
_Avoid_: 任务、作业、供应商调用。

**供应商调用**：
ArcReel 在执行生成任务时向外部生成服务发起的一次调用；它是生成任务的执行细节，不是创作者管理的任务。
_Avoid_: 供应商作业、生成任务。

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

**GenerationContext**：
一次生成任务执行前 provider 解析的**全部产物**，由唯一入口 `resolve_generation_context` 在单个 ConfigResolver session 内交付：`generator`（MediaGenerator）加上各**声明 lane**（image / video / audio）的结果值对象。消费方一次调用拿全，不在拿到 generator 后重开 session 二次解析。lane「传即声明」——任务只为自己用到的 lane 付出配置要求与构造成本；未声明的 lane 经 property 访问直接抛错（fail-loud，返回类型非 Optional）。任一声明 lane 的解析或构造失败即整次调用失败——无部分结果、无跨 provider 静默兜底；仅能力查询失败降级空值放行（见 `docs/adr/0049`、`docs/adr/0002`）。
_Avoid_: 把它当 MediaGenerator 的一部分——MediaGenerator 只管「怎么生成」，「怎么选的 provider」由外层 context 承载；部分成功语义（声明的 lane 缺结果却返回残缺 context）。

**lane 结果的两组身份（`provider_model` 与 backend 实际身份）**：
每条 lane 结果同时携带 `provider_model`（规范 registry 身份，即选身份的产物）与 `backend_name` / `backend_model`（backend 构造后报告的实际身份）。**能力与分辨率查询按实际身份取值**，查询键是 `(provider_model.provider_id, backend.model)`：provider 轴用规范 `provider_id`（backend 在构造缝中不漂移，且族别名 provider 的 `backend.name` 是族名、非 registry key，不能作 provider 轴——如复用 Ark backend 的族其 `backend.name` 恒为 `ark`）；model 轴用 backend 实际 `.model`（自定义供应商目标 model 被禁用时 loader 静默回退，实际 model 才是唯一真实漂移轴）。grid 落盘的 model 元数据同理记 backend 实际 `.model`，身份发散时与解析意图的 model 可不同。
_Avoid_: 用 `backend.name` 作 provider 查询轴；假设 `provider_model.model_id` 恒等于 `backend_model`。

**文本任务档位（text task tier）**：
文本生成调用点的粗粒度分级，取值 **简单** / **复杂**。每个走文本管道（TextGenerator）的调用点在代码里固定归属一档；用户配置的是「每档用哪个文本 backend」，不配置映射本身。每档一个设置项，另有一个「默认模型」作为各档未设置时的回退；解析顺序项目优先：项目档位 > 项目默认 > 全局档位 > 全局默认 > 自动推断。简单档包含需要 vision 的调用点（风格图分析），该档模型须支持图像输入。档位只管辖文本管道；Claude Agent SDK 的对话与 subagent 推理模型由 Agent 供应商配置决定，不在档位管辖内（见 `docs/adr/0051`）。
_Avoid_: 为单个调用点开专属模型设置项（档位即配置粒度的上限）；把 Agent 对话模型当作某个档位；把「默认模型」理解为第三档——它不绑定任何任务，只是回退。

**模型能力**：
一个模型支持的生成输入、输出和控制方式，例如是否接受参考图、是否生成有声视频或是否支持尾帧。
_Avoid_: 能力桶、把模型能力与用户选择哪个模型混为一谈。

**模型选择**：
为不同生成用途指定所用模型的设置；未单独指定时使用默认模型。
_Avoid_: 生成模型、能力桶、模型能力。

**capability（t2i / i2i / i2v / r2v）**：
媒体任务按请求形态的能力分类。图片两种：t2i 文生图（无参考图）、i2i 图生图（带参考图）——一个镜头属于哪种，取决于"开画那一刻"是否拼出了参考图，**只有执行时才能确定**（见 `docs/adr/0001`）；入队与调度（worker claim）这两个执行前环节都无法获知。图片编辑任务是唯一例外——它必然 i2i，入队即知（见「图片编辑」）。视频两种：i2v 图生视频（首帧驱动——分镜路线的全部镜头，逐张与宫格装配同归此桶；另承接参考路线无参考图退化镜头的降级执行）、r2v 参考生视频（参考图槽位驱动——参考路线的有参考图镜头）——视频先按项目生成路线定轴，参考路线内再按镜头是否携带参考图分流（执行层按解析后的实际参考图判定，入队预检 / 限流投影 / 费用估算按 unit 声明的 references 近似，判据在 `lib/reference_video/units.py`），三种 content_mode 同一口径（见 `docs/adr/0054`）。
_Avoid_: t2v 作为 capability 维度——参考模式无图镜头降级归 i2v，不另立形态。

**能力桶（capability bucket）**：
按 capability 细分的**可选**模型配置槽位（图片 t2i / i2i，视频 i2v / r2v），是「默认模型」之上的细化覆盖：未配置的桶回退同层默认模型。解析顺序与文本任务档位同构、项目优先：项目桶 > 项目默认 > 全局桶 > 全局默认 > 自动推断。桶与调用点的映射固定在代码里、用户只配桶内容；桶候选按能力预过滤，解析时能力不满足直接报错，不静默换模型（见 `docs/adr/0054`）。「能力桶」是代码与文档内部术语，界面呈现用「按用途指定模型」，不直接暴露给用户。
_Avoid_: 把桶当强制配置（默认模型才是唯一兜底层）；按生成路线逐路设桶（桶的维度是能力不是路径）；把「默认模型」理解为又一个桶——它不承诺任何能力，只是回退；在用户可见文案里写「能力桶 / capability bucket」。

**执行模型（effective model）**：
给定调用点的 capability（或文本档位）与当前配置，分层解析最终选中的模型：细分桶 / 档位生效时是其中的模型，否则是默认层逐层穿透的结果，全层皆空时来自自动推断。与「默认层模型」相对——默认层只是解析的一层输入，执行模型才是真正会执行的那一个。凡按模型查能力或按模型存配置的界面元素与存储键（per-model 设置，如分辨率），一律取执行模型；视频侧先按项目生成路线定桶再求值，项目内全集同一结果——参考路线的无参考图退化镜头实际由 i2v 桶模型降级执行，两桶配了不同模型时该镜头的执行模型与此处求得的不是同一个（见 `docs/adr/0054`）。全层皆空的自动推断依赖供应商就绪状态与 registry 顺序，前端算不出：此时按模型查能力的界面元素显示「自动选择」、`model_settings` 省略该项，不伪造一个执行模型；后端侧无此限制，自动推断结果本身就是执行模型。裸 provider（未带 `/model`）覆盖时，后端 `_parse_project_provider` 会展开为该 provider 的默认 model 再求执行模型，前端 `effectiveModel()` 不做此展开、原样返回裸字符串——两侧对同一层的执行模型取值可能不同，UI 存取键与后端查询键因此不一致，属已知例外。
_Avoid_: 用默认层模型作能力查询或 per-model 存储的键（细分覆盖生效时两者不同）；把执行模型当作可配置项——它是解析结果，不是配置槽位；把执行模型等同于生成期 lane 结果的 backend 实际身份——后者是构造后的查询键（见「lane 结果的两组身份」），两者仅在自定义供应商 loader 未回退时保证一致。

**执行身份（execution identity）**：
某次视频任务入队时冻结的「provider + model」快照，执行与孤儿任务续跑（`resume_executor.py`，worker 层：任务已提交、进程中断后按原身份续轮询）都按它进行，不随此后的配置变化漂移。冻结动作称**锁定（pin）**。与「执行模型」相对：执行模型是按当前配置解析求值的结果、随配置变，执行身份是把彼时的执行模型冻结后的快照，从最终提交给 provider 那一刻起不再变（参考路线降级镜头有一次入队到提交之间的改写，见下）。锁定只发生在视频任务：音频/TTS 任务入队只派生 provider_id 供 claim 过滤、不锁定 model；图片任务不锁定（任务周期短，配置漂移窗口小）；视频任务的锁定本身是 best-effort——入队时派生执行模型失败会静默留空，此时没有身份可锁，任务退回按当前配置逐次求执行模型（`lib/generation_queue.py::_derive_execution_model_for_enqueue`）。身份的 endpoint 维度不随 provider+model 在入队时一并冻结，而是任务提交拿到 `provider_job_id` 时才经统一收口点（`lib/video_backends/base.py::_persist_provider_job_id`）持久化，按维度分落两列（与「endpoint（协议端口）」词条同名但非同一概念，那是 `ENDPOINT_REGISTRY` 里的协议槽位本身，这里是持久化的执行期取值）：**协议维度**只有自定义供应商有，落 `provider_endpoint` 列记协议标识——它决定协议，provider/model 不变也可能换 backend——续跑据此比对，不一致即显式失败；**连接维度**记当次实际使用的请求域名，续跑经 `submitted_base_url` 回放该域名轮询、不比对当前域名，自定义供应商记在同名的 `submitted_base_url` 列（其 `provider_endpoint` 位已被协议标识占用），内置供应商无协议维度、直接记在 `provider_endpoint` 列。域名只由走 dashscope 协议的 backend 记与消费（自定义供应商委托该协议时同样适用），内置侧即 DashScope（含 wan3.0），其余内置供应商（如 Gemini、Ark）不落此值，续跑按当前配置的 backend 直接轮询。排队中未提交的任务则照常按新配置提交。参考路线入队按 unit 声明近似锁定、提交前按实际参考图把身份改写为实际执行的那一个，孤儿续跑因此始终跟随实际执行过的 backend；锁定的身份续跑前只校验 provider/model 存在性，endpoint 维度仅自定义供应商额外比对，不重跑能力闸（见 `docs/adr/0054`）。
_Avoid_: 用「执行模型」指代已冻结的身份（一个随配置变、一个不变）；把锁定当能力闸（闸在那个身份被锁定之前已过，锁定只锁身份；入队锁的身份闸在入队时过，参考路线降级为 i2v 的改写身份闸在执行期改写前才过）；换身份续跑（等于拿另一个 backend 轮原身份的 `provider_job_id`）；把「视频任务会锁定」当无条件保证——派生失败时不锁；把这里的「续跑」与 agent 工具层的 `resume=true`（未完成镜头重新入队提交，按当次配置求新的执行身份，不是同一机制）混为一谈；把 endpoint 维度当入队时随 provider+model 一并冻结——它冻结在提交那一刻；把落请求域名当所有供应商的通用行为——只有 dashscope 协议这条线记与回放域名，其余内置供应商续跑不经域名回放；把两个维度当同一格取值——协议标识与请求域名各占一列，拿标识当域名拼 URL 只会把可归因的 404 换成更难归因的连接错误；把「音频/图片任务不锁定」误读为它们没有执行模型——「执行模型」词条对全部任务类型都适用，只是「锁定」这个冻结动作只对视频任务发生。

**图片编辑**：
根据创作者的编辑指令修改一张已有图片，并保留未要求改变的主要内容。图片编辑产生新版本，不会改写生成原图所用的图片提示词。
_Avoid_: 重新生成、局部重绘、把编辑指令当作图片提示词保存。

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

**时长联动约束（duration_resolution_constraints / reference_image_durations）**：
在 `supported_durations` 全集之上按上下文收窄的两个 per-model 声明：前者是 `{分辨率: 允许时长}`（如 Veo `{"1080p": [8], "4k": [8]}`），后者是走参考图路径时的允许时长（如 Veo `[8]`）。两条各自独立触发、可同时生效、取交集；与 `supported_durations` 同为 registry 单一真相源。后端唯一收窄入口是 `lib/config/resolver.constrain_durations`：剧本生成的 prompt 与动态 schema、SDK MCP 工具交给 LLM 的候选、执行期未显式指定时长时的取值，都在下传前经它收窄；前端时长选择器（项目级默认与逐镜头 pill）按同一份声明过滤候选。约束求值用的生效分辨率（`_resolution_for_constraints`）取项目已保存的档位，前后端同口径；未保存时不施加分辨率约束——普通视频路径此时省略 SDK 的 resolution 参数，供应商按自己的默认档位处理（Veo 是 720p），该档位下全集本就合法。参考视频路径例外：它执行期下发 `resolution_or_fallback`，故未保存时按 provider 兜底档位求值，与实际下发的档位保持同一集合。已保存的越界时长一律给「警告 + 引导重选」，不静默改写。backend 侧另有模块级兜底常量，只在型号未登记于 registry 时生效（中转站、自定义供应商包装、已下线型号）。
**参考图约束逐 unit 生效，不按集一刀切**：参考路径允许 unit 不带任何引用，执行层与 backend 都只在 `reference_images` 非空时施加它，故一个 unit 的生效档位取决于它自己有没有 `@[名称]` 引用。全链路同此判据——step1 拆分把「带图 / 不带图」两套档位一并注入 prompt、schema 枚举取其并集、references 从正文机械派生后逐 unit 判归属（`sdk_tools/_context.reference_unit_duration_tiers`），step2 按**最终**产出的 references 重算（`_unit_duration_off_tier`），预检与执行按落盘 references 重算（`precheck_unit` / `effective_reference_durations`），前端下拉按选中 unit 的 references 切换候选。两套档位之间不假定包含关系：`constrain_durations` 在交集为空时回退到未收窄候选，型号声明自相矛盾时带图那套反而更宽，故并集须显式求。
_Avoid_: 把它当通用「条件→约束」DSL 的雏形去扩展——只表达已有官方明文的联动维度；在 backend 里另写一份与 registry 平行的约束表；拿「带图」那套当整集的上界（会收掉无引用 unit 本可申请的短档）。

**default_duration**：
项目级偏好时长（int）；为 null 或缺失时是一个有语义的「auto」档——由 AI 按内容节奏在 supported_durations 内自行决定，**不是**「未设置 / 待填」。
_Avoid_: 把 null 读成「未配置」而擅自补默认值；与分镜级逐个时长选择混为一谈。

**「不传」语义（resolution = None）**：
分辨率作为**纯清晰度**且 SDK 非必传时，未配置即解析为 None——含义是「调用 SDK 时不携带该参数」、走 SDK 自身默认，而非我方填兜底默认值；`DEFAULT_VIDEO_RESOLUTION` 等我方默认表已删除（见 `docs/adr/0019`）。
_Avoid_: 把 None 当「用某个默认分辨率」而擅自填值。注意当尺寸须**承载比例**时不适用——该场景由 `aspect_size` 始终计算并下传（见 `docs/adr/0011` 与「尺寸」「分辨率」条）。

### 参考图与压缩

**原图**：
用户上传的、用于表达主体真实外观或创作意图的源图片。原图可以在后续生成中作为参考图，但不会因此变成资产图。
_Avoid_: 资产图、把所有上传图片都叫参考图。

**参考图（reference image）**：
在某次生成中作为条件输入、用于引导身份、风格或构图的图片。原图、资产图和其他生成结果都可以在一次具体生成中充当参考图。
_Avoid_: 把参考图当作图片的永久类型、把生成产出一概称为参考图。

**参考资产**：
创作者为一个视频单元选择、用于提供参考图的项目资产；选择顺序属于创作者意图。一个参考资产在生成时可以展开为一张或多张参考图。
_Avoid_: 参考图、把正文中的资产名称自动视为最终参考资产选择。

**参考上传副本（reference upload copy）**：
把参考图编码进供应商请求体那一刻所用的**那份字节数据**。是临时副本（内存缓冲 / 临时文件），用完即删；不是磁盘上的源资产文件，也不是生成产出。三者必须分清：**源资产文件**（如 4K `character_sheet.png`，只读）、**生成产出**（模型返回的成品，全质量落盘，无保存时压缩）、**参考上传副本**（唯一会被压缩的对象）。
_Avoid_: 把「压缩参考图」误读为压缩源文件或产出。

**参考图压缩（reference image compression）**：
仅对**参考上传副本**做的等比缩放 + 重编码，目的是在不超出供应商请求体大小上限的前提下、尽量不损伤条件效果。因其只动发完即删的副本，对源资产与产出**零影响**——「生成 4K 却拿不到 4K」在此机制下不可能发生。决定压到多大属于「目标模型」决策，不属于本术语表（见 `docs/adr/0012`）。
_Avoid_: 把它与上传保存时压缩（`normalize_uploaded_image`，针对用户上传）混为一谈。

### 计费

**生成费用**：
使用模型完成内容生成所产生的费用。提交前显示预计费用，调用完成后记录实际费用，无法关联到具体内容的费用显示为未归属费用。
_Avoid_: 成本、成本快照、费用归属。

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
为旁白或解说文本生成的独立语音素材，可与画面在后期合成。
_Avoid_: 生成旁白、视频声音、TTS 音频。

**生成有声视频**：
由视频模型在生成画面的同时生成与画面同步的声音。
_Avoid_: 生成音频、旁白配音、视频音频。

**音色（voice）**：
TTS 供应商内置的一组预设发音人，合成请求以 `voice` 参数携带其 id（如 DashScope 的 `Cherry`、OpenAI 的 `alloy`）。各 audio backend 以 `list_voices()` 交付自己的音色目录，目录内容一律取自供应商官方文档并保留可追溯的官方来源链接，不凭印象填写或在仓库内保存官方文档正文副本。解析产物随 audio lane 的 `voices` 交付（值，非 backend 实例，见 `docs/adr/0049`）。
_Avoid_: 用 voice 指代 audio 媒体类型本身；把音色与「声音复刻（voice cloning）」混为一谈——前者选供应商预设，后者用参考音频克隆。

**音色试听**：
用于试听和确认某种音色的短音频，不属于成片素材。
_Avoid_: 语音试听样本、旁白配音、参考音频。

**声音一致性档位（voice consistency）**：
视频模型在跨片段保持人物音色上能做到什么程度的三级标识，由「模型有无音轨」×「项目生成路线」二维派生，全仓库唯一派生点是 `lib/config/resolver.py::derive_voice_consistency`。路线创建即定不可变，同一项目内档位不随剧集或剧本变化。`native`＝参考路线直传参考音频、音色由音频本身锁定；`soft`＝有音轨但只能靠文字描述引导音色；`none`＝真无声，不承载任何声音语义。soft/none 之分不看 `generate_audio` token 是否声明——该 token 语义是「开关可控」而非「有无音轨」，恒有声但开关不可控的型号另由 `ModelInfo.audio_always_on` 逐型号声明，经 `model_has_audio_track` 与 token 合成为有音轨。恒有声按型号而非按供应商声明：同一供应商名下可以部分型号恒有声、部分型号可开关或无声。音轨的另一位描述是**开关可控性**（`model_audio_switch_controllable`，即 token 的字面语义）：设置界面按它决定音频开关是否可交互，恒有声与恒无声两类模型的开关置灰并展示成片的实际音轨状态；存量配置里的「关闭」由入队前预检显式拒绝（判据单一真相源 `server/services/video_caps.py::resolve_audio_switch_conflict`，WebUI 与智能体两条提交路径各自包一层出口），保证无声判据只在开关真正可控时才可能为假。
_Avoid_: 用 `generate_audio` 的真假直接代指有无音轨；把「开关可控」与「有音轨」当同一位读。

**声音描述声明段（Voice_Profiles）**：
drama 视频提示词 YAML 顶部的集中声明段，形如 `Voice_Profiles: [{Speaker, Voice_Style}]`，由编排层从角色资产的 `voice_style` 机械派生——收录集合为「本场景 dialogue 的 speaker」∩「角色资产 `voice_style` 非空」，只出场不开口的角色不收录。剧本 JSON 与 step2 LLM 零承载：编排层是它唯一的来源（`lib/prompt_utils.py::build_drama_video_prompt`），故角色 `voice_style` 改动下次生成即生效。无声时不注入——`none`（模型不产音）与本集关闭音频（`requested_generate_audio` 为假）同口径，入队前判据收在 `server/services/video_caps.py::resolve_project_is_silent`、执行期收在 `VideoLaneResult.is_silent`；台词不看这一位，无声成片里照常下发供口型参考。
_Avoid_: 与既有 `Dialogue` 条目混为一谈——前者声明音色、每 speaker 一条，后者是台词、按时序逐条。

**"audio" 的三种含义（歧义警示）**：
- **audio（媒体类型）** = 本表定义的 TTS 维度。
- **`generate_audio`（能力/字段）** = 视频模型（Veo/Kling 等）**自带音轨**的开关，属 video 维度，与 TTS 无关。
- **`ambiance_audio`（脚本字段）** = 喂给视频模型的**环境音效提示词**，是文本而非音频文件。
新增 TTS 相关命名一律避开 `generate_audio` / `ambiance_audio` / `resolution_audio`（Veo 视频计费维度），防止与 audio 媒体类型混淆。

### 项目与资产

**项目**：
ArcReel 中一项完整的视频创作，拥有自己的源文件、脚本、资产、生成模式和成片内容。
_Avoid_: 作品、工程、用剧集指代整个项目。

**集**：
项目中的一个连续内容单元。广告/短片项目也只有一集，但界面可以隐藏集选择。
_Avoid_: Episode、单集、用剧集同时指一集和整个系列。

**分镜数**：
分镜图生视频项目中分镜的数量。
_Avoid_: 场景数、片段数、把视频单元计入分镜数。

**视频单元数**：
参考生视频项目中视频单元的数量。
_Avoid_: 场景数、分镜数。

**镜头数**：
视频单元内部镜头的数量。
_Avoid_: 场景数、分镜数、视频单元数。

**资产图**：
为角色、场景、道具或商品确定标准视觉形象的图片，供同一项目中的后续创作复用。资产图用于某次生成时，也同时充当该次生成的参考图。
_Avoid_: 设计图、设定图、参考设计图。

**场景**：
可在多个分镜或视频单元中复用的环境资产，描述故事发生的地点与空间外观。
_Avoid_: 用场景指代剧本中的分镜、剧情段落或视频单元。

**角色**：
在项目内容中出现、可被多个分镜或视频单元复用的人物资产。
_Avoid_: 人物、人物资产、character。

**项目资产**：
当前项目拥有的角色、场景、道具和商品；它们只属于该项目，不会与全局资产库自动同步。
_Avoid_: 资源库、设定集、角色集、场景库、道具库。

**资产名坐标系（asset name normalization）**：
项目资产名的判等坐标系是 **strip + Unicode NFC、大小写敏感**，函数集中在 `lib/asset_types`。`character / scene / prop / product` 共用一个项目级名称空间，任何两项不得同名；登记闸口 `validate_asset_name` 把新名落成 strip + NFC 形态，schema v6 迁移会把存量 key、引用、媒体与版本历史一次性级联收敛。`ASSET_SPECS.namespace_priority` 定义存量冲突的稳定所有者优先级；同类 Unicode 等价条目延续迁移前的后写胜出语义，其余条目获得类型后缀新名。全局资产库 DB 不在这个名称空间内。
_Avoid_: 在业务读取或渲染路径保留跨类型同名的优先级消歧、双读兼容或 registry 参数；把全局资产库的名字约束扩大到项目外。

**资产重命名（asset rename）**：
以 name 为身份的资产改换名称的**原子级联事务**：资产桶 key、全部剧集剧本中的名称引用（各骨架引用数组、说话人 speaker、`@[名称]` mention）、按名命名的关联文件（设计图/参考图/参考音频/版本快照）及其路径字段一次改齐，维持「文件 stem = 资产名」不变式。目标名与项目内任一资产冲突即拒绝。全局资产库不联动（快照复制语义，库有独立改名入口）；不与进行中的生成任务互斥，属已知限制。
_Avoid_: 用「新名 upsert + 删旧名」拼装改名——引用会断裂、旧名残留；目标名已存在时并入——那是合并，另一种语义，重命名不承载；把它与全局库改名传导混为一谈。

**全局资产库（global asset library）**：
跨项目保存和复用角色、场景与道具的资产集合。资产应用到项目后成为独立的项目资产，之后两边的修改互不影响。
_Avoid_: 项目资产、资源库、以为库内资产与项目资产会自动同步。

**商品**：
广告/短片中需要准确呈现和推广的商品资产，以用户上传的商品原图作为外观真实性依据。
_Avoid_: 产品、产品资产、商品角色。

**风格模版（style template）**：
预置的整段画风 prompt 文本（真人 / 动画两类，按 id 选一）。选定时把展开后的 prompt 写入 project.json 的 `style` 字段（供注入用的快照），同时保留 `style_template_id`（可在 PATCH / 读时迁移被重新解析）；registry 改动不主动回写老项目（见 `docs/adr/0023`）。
_Avoid_: 把 style 理解为短标签（旧值 Photographic/Anime/3D 已废，仅作 legacy 别名懒迁移）；与风格参考图（`style_image`，用户上传的画风参考）叠加——二者互斥，写入一方即清除另一方。

**线索（clue）— legacy 资产术语**：
ArcReel 早期对「场景 + 道具」的统称（按 type 区分 location/prop）；现已拆为独立的 scene 与 prop 两类资产，clue 及其 `importance` 字段不再是当前数据模型的概念。
_Avoid_: 在新代码/文档里用 clue/线索 指代场景或道具——规范词是 scene 与 prop；仅在读历史 project.json、迁移代码与归档设计稿时会遇到 clue。

### 剧本与分镜

**创作类型**：
项目内容如何组织和表达的分类。它与生成模式相互独立，同一种创作类型可以采用不同的生成模式。
_Avoid_: 内容模式、把创作类型与视频生成方法混为一谈。

**旁白/解说**：
以连续的旁白或解说组织内容、由画面配合讲述推进的创作类型。
_Avoid_: 旁白模式、说书模式、说书+画面、旁白驱动。

**剧情演绎**：
以角色行动、对白和剧情场面组织内容的创作类型，可采用真人、动画等不同视觉风格。
_Avoid_: 剧集动画、内容驱动、drama 模式。

**骨架（skeleton / 骨架种类 skeleton kind）**：
剧本条目数组的结构种类，四值：`segments`（说书片段）/ `scenes`（剧集场景）/ `shots`（广告镜头）/ `video_units`（参考视频单元）。骨架由 content_mode 与生成路线两轴**派生**，本身不是第三条轴：分镜路线按内容模式分别使用前三种骨架，参考路线三种内容模式统一使用 `video_units`；`docs/adr/0033` 中“广告骨架恒为 shots”的决定仅继续适用于 ad + storyboard。路线一轴恒取项目字段，剧本自身不承载路线信息。对骨架有两种合法提问——**规范性**（按项目的 content_mode 与生成路线，这份剧本*应该*是什么骨架）与**取证性**（这份剧本数据*实际*是什么骨架）；两者在存量失配剧本（骨架与项目路线不符的历史集）上可能不一致，取证以数据形状优先。骨架知识收归零依赖叶子模块 `lib/script_skeleton.py`：以骨架种类为键的窄表 `SKELETONS`（键即条目数组键，行 `Skeleton(id_field, chars_field)`，`video_units` 无逐条角色名单故 `chars_field=None`）+ **规范解析** `resolve_declared_kind(content_mode, generation_mode)`（服务手持项目配置的消费方，未知/缺失 content_mode 抛 `ValueError`）+ **取证解析** `resolve_script_kind(script)`（服务手持剧本数据的消费方，保留数据形状优先的容忍阶梯）；两个解析器是全体消费方分派骨架的单一入口，设计依据见 `docs/adr/0045`。智能体的生成入队工具与数据校验另过**路线闸门** `ensure_route_skeleton(script, content_mode, generation_mode)`：剧本骨架与项目路线跨族（分镜族 ⟷ `video_units`）时抛 `SkeletonRouteMismatchError`，给结构结论与重拆指引，杜绝静默降档与悄悄换路径；族内形态差异与残留的另一族数组均放行。查看 / 编辑 / 项目归档导出不经闸门，失配剧本仍可读可改可归档；剪映草稿导出按剧本 content_mode 的规范骨架取片段，失配剧本取不到已完成片段。
_Avoid_: 把骨架当第四个 content_mode 或 content_mode 的同义词（三值轴推不出四种骨架）；把规范性与取证性两问混同（存量失配集的骨架与项目路线不符时，编辑要跟数据走、生成要跟路线走）；对未知模式做「非 narration 即 drama」式二值兜底（`docs/adr/0033` 禁令）。

**生成模式**：
项目创建时选定的视频生成方法，决定视频由分镜图还是资产参考图驱动；同一项目内所有剧集采用同一种生成模式，创建后不可更改。
_Avoid_: 生成路线、生成方式、视频来源、与创作类型混为一谈。

**分镜图生视频**：
先为每个分镜生成分镜图，再以分镜图驱动对应视频生成的生成模式。
_Avoid_: 图生视频模式、分镜模式、storyboard 模式。

**参考生视频**：
以角色、场景、道具或商品的参考图直接驱动视频生成的生成模式，不需要先为每个分镜生成分镜图。
_Avoid_: 参考视频、参考模式、参考直出。

**多宫格分镜**：
把多个分镜合并成一张多宫格图统一生成、再拆成各分镜图的生产方式，用于增强画面风格与主体的一致性。
_Avoid_: 分镜板、故事板、宫格装配、切分落格、把它当成与其他生成模式并列的模式。

**尾帧（end frame / end_frame_image）**：
用户为单个镜头指定的、视频生成收束到的目标画面——普通图生视频路径上的**可选**过渡控制手段（首帧恒为分镜图，不开放自定义）。是镜头条目的**用户意图持久属性**（存剧集 JSON，视频重生成自动沿用），不是生成产出；来源为项目内选图或上传任意图，落定即**快照复制**进项目专用目录、与源图彻底解耦（源图重生成/回滚/删除不影响已定尾帧，跟随源图更新须手动重选）。所选后端不支持 last_frame 能力、或快照文件缺失时硬失败，不静默降级。
_Avoid_: 与宫格产出字段 `storyboard_last_image`（运行时产出，已不再作尾帧消费）混为一谈；把整集剧本重生成后字段丢失当 bug——与 note/transition_to_next 同口径，「重生成沿用」仅指视频重生成；用它做全自动场景衔接（正常成片切镜是合理且应该的）。

**广告/短片**：
围绕单条成片及传播目标组织内容的创作类型，适用于商品推广、品牌传播和独立创意短片。
_Avoid_: 广告模式、短片模式、把它限定为带货视频。

**分镜**：
以分镜图驱动视频时，剧本中可独立编辑和生成的一条内容。旁白/解说、剧情演绎和广告/短片都以分镜组织剧本。
_Avoid_: 场景、片段、剧本场景、把分镜与分镜图或镜头混为一谈。

**分镜图**：
为一个分镜确定构图和起始画面的图片，也是该分镜生成视频时的画面输入。
_Avoid_: 分镜、资产图、多宫格分镜。

**视频单元**：
参考生视频时，一次视频生成、计费和成片归属的最小内容单位。视频单元以一段正文表达按时间排列的镜头，并独立保存创作者选择的参考资产。
_Avoid_: Unit、参考视频、场景、片段、把视频单元与其内部镜头混为一谈。

**镜头**：
视频单元内部按时间编排的画面单位；它描述局部画面变化，但不单独生成成片或计费。
_Avoid_: 分镜、视频单元、shot。

**三段论渲染（参考生视频路径）**：
三种内容模式共用同一套参考视频渲染管线，发给视频模型前的机器渲染形态，三段各有归属：第一段是参考来源声明区——主体绑定 + 声音声明（`<X>的台词音色参考 @音频N，声音特征：…`）；第二段是镜头分镜段，台词行渲染 `<X>说 {台词}` / `画外音说 {台词}`；第三段是风格锚定与画质/稳定/字幕/水印约束包。第一、三段由渲染期机械生成，不依赖 LLM 自觉；渲染是纯函数、结果不落盘。整段文本不含绝对秒数，编排时长经预检投影后的申请时长走请求字段。三种模式的输入均为统一书写层自由文本，广告不再保留结构化镜头专用渲染器。声音注入按 `voice_consistency` 分档：`native` 才绑参考音频，`native`/`soft` 均注入 `voice_style` 声音特征。无声路径不注入声音声明但保留台词渲染；音频编号按 dialogue speaker 首现顺序并与 `reference_audio_files` 请求字段顺序绑定。
_Avoid_: 让 LLM 书写第一段或第三段；给广告参考路线保留结构化镜头专用渲染分支；把角色与音频的对应关系塞进请求字段；解析预览与生成各自重算声音绑定。

**书写层文稿（参考生视频）**：
参考路径上「一个 unit 的正文」的统一表达——按行书写的扁平文本，只有镜头行（`镜头N：`）、规范台词行（`@[角色]：{台词}`）、画外音行（`{台词}`）三种，产品、角色、场景与道具统一写 `@[名称]`。人在编辑器里写的、narration/drama 两级产出的、ad 单次产出的是同一种格式，故语法只有一份真相源（`lib/reference_video/writing_syntax.py`，与解析器同域）。**LLM 只写内容，机器写结构**：unit_id 按序编号、shots 按镜头行切分、references 与 utterances 从正文派生，都不进 LLM 输出。narration/drama 的 step1 决定 unit 边界与编排时长，step2 只做视觉展开；ad 无 step1 审阅 gate，直接产出相同书写层。混合人物发声与无归属旁白的 unit 保留原内容并标记 `needs_replan`，下游不自动拆分、移动、删减或改写。
_Avoid_: 让 step2 改台词以迁就画面；把语法规范再抄一份到 agent 文档或 prompt 里；把机器可派生的字段（unit_id / references / utterances）交给 LLM；为广告另造一套书写层或在下游自动修复混合发声。

**隔离草稿（参考生视频违约产物）**：
机器产物违约时的处置形态——正式文件一步不动，违约产出连同逐条违约报告落到同目录的 `*.invalid.json`（step1 / step2 各一份），由在场 agent 修复后晋升，不丢弃重抽。生成一次即计费，重抽既烧钱又不收敛（同一模型对同一份输入大概率再犯同一类错）。信封是 `{kind, episode, meta, violations[], content}`：`content` 装**扁平书写层产物**（LLM 面的形状），结构字段仍由机器派生——让 agent 编辑派生物等于给漂移开口子；`violations[]` 每条带违约类（`code`）与 unit 定位（`label`），只是上一轮判定的快照，晋升时一律按 `content` 现值重判。晋升走的是产出时那套校验器本身而非它的副本，杜绝「晋升放行、下次生成被拒」的分叉；仍违约则报告刷新、草稿留在原地继续改，**无收敛轮次上限**（每轮都带着具体定位在改，不是碰运气）。隔离草稿在场期间审阅 gate 拒绝确认、step2 拒绝生成——它与「正式文件的内容指纹」是两件事：重拆分违约时正式文件原封不动，只看指纹会把该集判成已确认并放行上一版内容。schema 层（JSON 不合法 / 外层形状不符）不进此机制，仍由 backend 重试兜底——但草稿是 agent 手改的，晋升时 schema 与内容约束一并重判，改坏字段同样只回报告。正式 step1 一旦重新落盘（重拆或晋升），在场的 step2 隔离草稿随之清除：它的保结构 diff 以旧 step1 为基底，留着既晋升不了又会一直阻塞生成。声音降级的三类提示（角色未设参考音频、参考音频段数超上限、无声模型知会）不是违约：照常落盘，随产物一并呈现。
_Avoid_: 把违约产物丢弃后重抽；让 agent 在隔离草稿里手写 unit_id / shots / references；给晋升加轮次上限或「超过 N 轮就重抽」的兜底；把隔离态与审阅 gate 的 pending 混为一条出路——前者要 agent 改草稿再晋升，后者要用户去 Web 端确认。

**发声条目（utterance）**：
drama 场景里「说出来的话」的统一单元——每条要么是角色台词（有说话人），要么是画外音 / 旁白（无说话人）。一个 `DramaScene` 持有一条**有序**发声序列（`utterances`），插入顺序即幕内先后（台词与画外音交错的先后由此表达）。类型决定下游去向：台词进视频生成、由供应商生成口型音轨；画外音不进视频，留给成片字幕与日后 TTS。drama 的口播内容以此为单一真相源；narration 的口播不走 utterances，仍是被朗读的 `novel_text`。参考生视频路径复用同一类型但不落盘：分镜文稿是唯一真相，utterances 按行读时派生（规范台词行 → 台词、裸 `{…}` 行 → 画外音、混写在描述行的花括号不派生只出提示），归属镜头级，存量文稿无台词符号时自然为空。
_Avoid_: 把台词与画外音当两个独立无序字段（先后会丢、下游要拼两源）；把 utterance 与说书 `novel_text` 混为一谈——后者是整段被朗读的原文（基数为一）、前者是场景内逐条发声（基数为多）；把画外音塞给视频供应商音轨——供应商音轨只承载口型台词，画外音走字幕 / TTS。

**对应原文**：
一个分镜或视频单元所依据的小说或剧本文本，用于创作者对照来源，不会作为独立内容被朗读或生成。
_Avoid_: 原文锚、源文切片、source_text、把对应原文当作旁白文本。

**源文件类型**：
用户上传文本的类型，分为小说和剧本；它决定 ArcReel 是进行内容改编，还是优先保留作者已经完成的剧情与对白。
_Avoid_: 源文件性质、剧本源、与创作类型或生成模式混为一谈。

**小说**：
供 ArcReel 改编为脚本的叙事文本源文件。
_Avoid_: 原作、小说源、脚本。

**剧本**：
用户上传的、已经包含剧情组织与对白安排的文本源文件。ArcReel 应优先保留其中可听见的内容，并补充视频制作所需的信息。
_Avoid_: 剧本源、成品剧本、脚本。

**脚本**：
ArcReel 根据小说、剧本或创作要求整理出的结构化创作内容，用于后续生成分镜或视频单元。
_Avoid_: JSON 剧本、结构化剧本、用剧本指代 ArcReel 的结构化产物。

**内容整理**：
ArcReel 把源文件或创作要求整理为可供创作者检查的脚本内容的阶段。
_Avoid_: step1、预处理、内容层。

**内容待确认**：
脚本内容已经整理完成、正在等待创作者确认后继续生成的状态。
_Avoid_: 审核 gate、门禁、pending。

**待修复草稿**：
生成结果未满足内容约束、需要修正后才能继续使用的草稿；已有正式内容不受它影响。
_Avoid_: 隔离草稿、违约产物、quarantine。

**分集账本（episode ledger）**：
project.json `episodes[]` 即分集单一真相源：条目在 episode/title/script_file 之外扩展 `source_range`（原文素材范围）、`hook`（集尾钩子）、`outline`（drama 分集大纲）与 `ledger_status`（消费状态）；物理 `source/episode_N.txt` 是派生物（见 `docs/adr/0031`）。账本字段全部可缺失——`source_range` 缺失即该集没有位置记录（旧拆分流程写入、或手动预拆分上传），消费链路继续使用现有物理文件，但规划无法续接：plan 一律拒绝并指引全量重置；部分重置只在这类条目落在保留段时拒绝，落在清除范围内的随重置正常清除。
_Avoid_: 以物理集文件的存在性推断分集状态或集数（Glob 推断是被替代的旧模式）；把账本字段与 StatusCalculator 读时注入的统计字段混为一类——账本持久化在 project.json，统计字段不落盘。

**ledger_status（消费状态）**：
账本条目的三态生命周期：planned（已规划未消费）/ consumed（已有下游产物：step1 中间文件、剧本或媒体）/ stale（该集号重新规划前已有下游产物，标记而非删除）。状态是咨询性的，位置真相在 `source_range`：能否重造派生文件、能否续接规划一律看它有没有，不看状态。
_Avoid_: 与读时注入的 `status`（draft/in_production/completed）混为一谈——同一条目上两键并存、语义不同；拿 ledger_status 判断该集有没有原文范围。

**归一化坐标系（normalized source coordinates）**：
source_range 与 planning_cursor 的字符偏移全部落在 `lib/episode_ledger.normalize_source_text`（Unicode NFC + 换行统一）的输出空间；按偏移切片源文前必须先对源文执行同一函数。
_Avoid_: 拿偏移直接切原始文件内容——NFD（macOS/越南语导入）或 CRLF 源文会错位。

**planning_cursor**：
project.json 顶层字段，下一批分集规划在源文中的起点（`{source_file, offset}`，null = 无规划进度），由规划工具在每次提交时前移。`source/_remaining.txt` 余文文件已废除，无人读取，规划与重置提交时将其清理。
_Avoid_: 把 `_remaining.txt` 当进度真相源（损坏即不可恢复正是账本要消除的旧模式）；把 cursor 当唯一起点依据——规划起点以账本内最后一集范围末尾与 cursor 的较后者为准。

**源文指纹（source fingerprint）**：
project.json 顶层字段 `source_fingerprints`（`lib/episode_ledger.SOURCE_FINGERPRINTS_KEY`）：候选源文件（`source/` 直下一级的 .txt/.md，排除派生集文件与下划线/点前缀文件）相对路径 → 归一化文本（`normalize_source_text` 输出）的 sha256。plan 每次提交对全部候选源文件重新计算并整体覆盖写入（非增量合并），检测「规划中途源文件被外部替换」——账本坐标一旦绑定某段原文，原文再变就意味着坐标失真。比对只针对「已记录」的文件：未记录（存量项目、或新发现源文件首次 plan）不参与比对、不阻塞规划；已记录文件当前指纹不同、或该文件从候选源文件中消失，均判不一致，出路是恢复已记录的原文内容或执行全量重置（部分重置的前置校验复用同一套比对函数）。plan 与部分重置均锁外预检查一次（快速失败，plan 因此不浪费一次模型调用）、锁内提交闭包内复核一次。plan 的锁内复核之外另有一道基线比对：本次调用实际读入的源文（入口快照的全部候选源文件 + 循环中途实际读入的新增文件）在模型调用期间被改动即拒绝提交，这道比对不看是否已记录，首次规划途中换源文同样被拦。全量重置清空整本账本，指纹字段随之清除；部分重置保留段已验证指纹有效，字段不变，留给下一次 plan 提交自然刷新覆盖。源文已耗尽、plan 无内容可规划而早退时不进提交闭包，若 `source_fingerprints` 字段缺失则单独补记该字段一次，避免游标已到底的存量项目永远拿不到基线。
_Avoid_: 把未记录文件「不参与比对」当漏洞去堵——这是存量项目与首次规划的必要逃生口，不是缺陷；以为每次提交只覆盖本批窗口涉及的文件——覆盖对象是全部候选源文件，不是增量合并。

**指令**：
创作者为一次内容整理或生成提出的自然语言要求，只在该次操作中生效；长期偏好由智能体记忆承载。
_Avoid_: 用户意见、创作要求、instructions、prompt。

**分集规划（plan）**：
服务端分集规划能力（`lib/episode_planner.EpisodePlanner` + SDK 工具 `plan_episodes`）：从 planning_cursor 起读一个源文窗口，调项目配置的文本模型一次规划窗口内所有剧情弧完整的集（标题/钩子/范围；drama 含分集大纲），schema 强约束 + 锚点存在/唯一/连续机械校验失败自动重试，同一把项目锁内写账本、派生集文件并清理残留。窗口取法带弹性：剩余全文不足窗口 1.2 倍时直接延伸到全文末尾，避免残余被迫单独成集。plan 接收可选常驻 `instructions`（用户分集意见，如按章节对齐切分，口径见「用户意见（instructions）」条）：非空时注入规划 prompt 并附带账本现算的全局进度（已规划集数、未规划余量、本窗口体量，按阅读单位计）供换算本批切分节奏；规划分多批时须由 agent 逐批重复携带，缺省/空白则与无意见路径的纯剧情弧行为逐字一致（不含全局进度分节）。新提交的集号若在磁盘上已有下游产物（剧本/step1/媒体），说明该集实际已被消费过，提交时直接标 stale（产物不删除），随结果附回，不阻断提交。账本内存在没有 `source_range` 的条目时 plan 一律拒绝执行并指路全量重置——这类集既无法重造也无法确定下一批起点；消费链路（剧本/媒体/状态/导出）不受此限。每集体量等全局性偏好经 `patch_project` 显式写入 `episode_target_units`，plan 只读不写该设置。末批即耗尽、再次调用已无新内容时，账本现算一份全局分布快照（累计集数、体量最小 5 集、体量中位数、`episode_target_units`）随摘要附回，供主 agent 对照用户结构性偏好核对、有偏差须向用户说明；常规批次只追加一行累计集数，不附完整快照。
_Avoid_: 让主 agent 自行读原文选切分点（peek/split 脚本是被替代的旧模式）；窗口字数/每批集数硬编码到指令——它们是工具内部默认，`planning_window_chars` / `planning_max_episodes` 项目设置可覆盖；在快照里定义「多小算畸小」——代码只报分布事实，语义判断留给主 agent；把提交时的 stale 标记当阻断——它只提示主 agent 需重做下游产物，提交本身照常成功。

**重置分集规划（reset_episode_planning）**：
`lib/episode_reset.reset_episode_planning` + 同名 SDK 工具，是用户对已规划内容的调整入口——把账本退回未规划状态的逃生口（见 `docs/adr/0032`）。`from_episode=1` 全量重置：除已消费集确认外不做前置校验，任何损坏账本状态都能执行成功，`episodes` 清空、`planning_cursor` 置 null、源文指纹清除。`from_episode>1` 部分重置：保留第 1..from_episode-1 集，前置校验账本形状干净（含整本 `episodes` 的非对象条目、非法集号、重复集号）、`from_episode` 为既有集号且保留段集号连续无缺口、退回点（第 from_episode-1 集）坐标结构完整、全部已记录源文指纹一致、保留段坐标落在当前源文界内且首尾相接（跨源文件时前一文件须已耗尽），任一不满足即拒绝执行并指路全量重置。坐标连续性校验只覆盖保留段——重置范围内的条目通过账本形状校验后无论有无坐标都直接清除。两种模式对波及已消费集（`ledger_status=consumed` 或已有 step1/剧本/媒体产物，取并集）均先返回受影响清单待显式确认（`confirm_consumed=true`）才执行；下游产物一律不删除；重置范围内 `source_range` 坐标结构完整的派生集文件删除（只查结构、不读源文校验范围是否仍有效，删除不因源文缺失或越界而改走留底），无原文范围记录或结构不完整的改名留底（避免被后续规划误当孤儿文件重新认领）。重置完成后带调整后的 `instructions` 重新分批调用 plan 即完成调整，若新集号与重置前的已消费范围重叠由 plan 侧的磁盘产物探测自动标 stale。
_Avoid_: 把重置当删除——留底是改名不是删除，内容保留；把部分重置的前置校验失败当可重试——须先全量重置或修复根因，非瞬时冲突；期待重置本身产出新内容——它只清状态，新内容仍要靠后续 plan 调用产出。

### 智能体运行时

**ArcReel 智能体**：
在 ArcReel 中理解创作者要求、协助组织内容并调用创作能力完成工作的智能体；上下文明确时可简称“智能体”。
_Avoid_: Agent、助手、创作助手、Copilot。

**子任务**：
智能体为完成一个聚焦目标而拆出的工作，主对话只展示其目标、状态和结果。
_Avoid_: subagent、子智能体、子时间线。

**SessionActor**：
每个 Claude 会话一个专属 asyncio task，串行化该会话对 `ClaudeSDKClient` 的所有协议调用（connect / query / 中断 / disconnect）；SDK 客户端并发调用不安全，actor 就是这条串行化边界（见 `docs/adr/0028`）。
_Avoid_: 与 ManagedSession（会话内存状态容器）混为一谈——actor 是执行通道、ManagedSession 是状态；直接调用 `client.disconnect()` / consumer_task 是已被替代的旧模式。

**Agent 启动失败（agent startup failure）**：
Agent 尚未建立可用运行环境时发生的系统故障，位于任何对话轮次之前。
_Avoid_: 与 Agent 轮次失败混为一谈；仅用一条缺失异常类型与原因链的字符串表示。

**Agent 轮次失败（agent turn failure）**：
Agent 已成功启动后，某一轮未完成的故障终态；它是系统故障事件，不是助手回答。
_Avoid_: 把 SDK 合成的错误消息作为普通助手回答；与 Agent 启动失败混为一谈。

**故障观测（failure observation）**：
ArcReel 在一次 Agent 启动失败或轮次失败中实际获得的上下文与原始故障事实；它是帮助排障和反馈问题的证据，不是根因结论，除可用于冒用身份或产生扣费的秘密值外保持原貌。
_Avoid_: 预设穷举上游错误分类；把未识别事实归一成“未知错误”；从错误文案推断根因；扩张为完整会话快照或独立的故障记录实体。

**SDK transcript（agent 记忆）**：
SDK 按自身协议写入的会话记录（DB 镜像或 jsonl），唯一职责是供 SDK resume 重建 agent 上下文——它是 **agent 的记忆**，格式与写入时机均由 SDK 决定，ArcReel 无权改造、不得混入 UI 专有条目（会被 resume 喂回 agent 造成污染）。
_Avoid_: 把 transcript 当 UI 对话时间线的数据源——UI 唯一读源是会话事件日志；向 transcript 写入服务端合成事件。

**会话事件日志（session event log）**：
UI 对话时间线的**唯一读源**：每会话一条单调递增序号（cursor）的事件序列，实时流、断线重连、历史回放三种场景读同一份。条目在**写入点定型**——SDK 消息流与服务端合成事件（用户消息受理、中断、子任务进度等）在入日志那一刻完成语义识别与规范化。定位是 transcript 的**物化视图**：可从 transcript 重放重建（旧会话首次访问时懒生成），删除不丢真相。用户消息由服务端**先写日志分配身份再回显**，前端不渲染任何本地合成消息。skill 调用条目只记 skill 名与入参，注入全文不进日志（全文只活在 transcript）。
_Avoid_: 把它当第二真相源与 transcript 对账——漂移的修复手段是重放重建，不是双向同步；把 UI 投影概念（turn 分组等）烧进日志条目——日志存稳定事实，投影留给读取端；在读取端做去重或语义嗅探——定型只发生在写入点一处。

**流式预览态（draft）**：
正在流式生成、尚未完成的 assistant 消息在服务端内存中的唯一预览表示，身份即其 `message_id`；消息完成时被同 `message_id` 的日志权威条目**精确替换**。不入日志、不落盘——服务崩溃即丢，与 agent 记忆一致（SDK 同样不记得未完成的消息）。断线重连时随首帧快照携带当前累积态。
_Avoid_: 用内容比对判断 draft 与已提交内容的重复——对应关系只认 `message_id`；把 draft 做成日志条目的 pending 状态（破坏日志 append-only）。

**消息改写（message rewrite）**：
用户对已发出的某条历史用户消息的编辑-重跑动作：等同于回到该消息发出前，用改写后的内容重新发出，原消息及其后的全部对话随之废弃。仅用户消息可改写（任意一条，含首条）；会话存在未决问答卡片时禁止改写，问答优先；agent 运行中改写会先自动中断当前轮次。文件与项目数据的副作用不随改写回退，界面明示。机制上由分支会话承接。
_Avoid_: 与图片指令式编辑的「编辑」混称——改写专指会话消息；做成原地修改历史——已有回复对不上被改的输入，历史不再自洽。

**分支会话（session branch）**：
承接一次消息改写的新会话：改写点之前的对话前缀成为新会话的全部历史，改写后的消息作为其首个输入；分叉点固定在用户消息边界。原会话整体保留为产品不可见的备份（标记 superseded 并指向新会话，列表隐藏，数据不删），事件日志的 append-only 契约不受影响——新会话日志按既有机制从 transcript 重放重建（实现取舍见 `docs/adr/0058`）。
_Avoid_: 与 SDK 原生 `fork_session` 混为一谈——后者只能从会话末尾复制整史，无法丢弃改写点之后的内容；原地截断原会话的 transcript 或事件日志——破坏 append-only 与断线续传契约。

**子时间线（subagent timeline）**：
同一会话内由 parent_tool_use_id 归组的 subagent 消息序列。subagent 的工具调用与回复作为带 parent 标记的日志条目**全量收录**，但主时间线上只呈现单一可折叠的子任务卡片（默认收起，显示描述+状态+进度），展开才见子时间线。
_Avoid_: 把 subagent 消息平铺进主时间线；只收进度事件不收内部消息——展开子时间线的前提是内部消息在日志里。

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

**会话 JWT（session JWT）**：
交互式登录签发的管理员凭证，通常可访问 ArcReel 的全部管理能力，包括 API Key 管理。API Key 管理路由当前以 `sub` 的 `apikey:` 前缀区分凭证；若运维人员把 `AUTH_USERNAME` 配置成以该前缀开头，真实会话 JWT 也会被误判并在这些路由收到 403。会话 JWT 泄漏仍视为完整管理员身份失陷。
_Avoid_: 把所有 bearer token 都叫 API Key；把 `apikey:` subject 前缀当作不可碰撞的显式凭证类型；把下载 token 当作已与管理员权限隔离的凭证（当前通用 JWT 认证路径仍接受它）。

**API Key**：
面向自动化访问的广泛权限凭证，可访问绝大多数业务与配置能力，但无权管理 API Key。它不是低权限或可安全公开的 token，泄漏仍属于高影响安全事件。
_Avoid_: 与会话 JWT 完全等同；scoped token（当前没有 scope）；把“不能管理 API Key”误读为普通有限权限凭证。

**下载 token（download token）**：
为项目导出签发的短时效（约 5 分钟）、绑定项目名且在有效期内可重复使用的 JWT（`purpose=download`），作为导出端点的 query param 唯一认证方式——端点自校验、不读 Authorization header，让浏览器原生下载的 URL 里不出现长效凭证。导出端点会校验用途与项目，但当前通用 JWT 认证路径不会拒绝它，因此它在有效期内也具有广泛管理员权限。其 `sub` 继承签发调用者：会话 JWT 签发的下载 token 通常连 API Key 管理也可访问；API Key 签发的下载 token 保留 `apikey:` 前缀，仍会被 API Key 管理路由拒绝。
_Avoid_: 把它称为一次性或低权限凭证；把登录 JWT 放进下载 URL。

**浏览器直发请求（browser-initiated request）**：
由浏览器自身发起、无法携带 `Authorization` header 的请求——`<img>` / `<video>` 的 src 加载、`EventSource` 订阅、原生下载导航。ArcReel 对这三处各有各的答案：SSE 用 query param 传长效会话 JWT，导出用下载 token，静态媒体不设防。
_Avoid_: 按"哪个端点"给这类请求分类——分类依据是**谁发起的请求**；把三种现状当作有意的分级设计。

**自带认证端点（self-authenticated endpoint）**：
不走 router 级 Bearer 依赖、在端点内部自行校验凭证的端点，成因一律是浏览器直发请求。注册时挂在 `self_auth_router` 上，与匿名可达的公开端点写法相同但性质不同。
_Avoid_: 与公开端点混为一谈——自带认证端点拦得住匿名请求，公开端点拦不住。

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
