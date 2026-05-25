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
GenerationWorker 内并发执行 task 的容量，维度是 **provider × media_type**（不是简单的 image/video 两条总通道）。每个 provider 各有独立的 image pool 和 video pool，默认容量分别为 `IMAGE_MAX_WORKERS=5` / `VIDEO_MAX_WORKERS=3`，可在 provider config 里覆盖。一个 provider 的 video 池满，**只阻塞该 provider 的 video 任务**，不影响其他 provider；但若用户的项目只配了一个 video provider，这等于阻塞所有 video 任务。
_Avoid_: concurrency limit（太泛）。

**ProviderPool**：
worker 内承载 slot 的数据结构（`lib/generation_worker.py:ProviderPool`），字段 `image_max` / `video_max` + 两个 `inflight: dict[task_id, asyncio.Task]`。inflight 字典是**worker 内存状态**，与 DB 中的 `status='running'` 必须配对维护——cancel 触发时由 worker 在 in-process task 字典里查到对应 asyncio.Task 后 `cancel()`，finally 收尾时从 inflight 移除并把 DB 从 `cancelling` 转 `cancelled`（见 `docs/adr/0006`）。`docs/adr/0006` 落地前 inflight 会出现「DB 改 cancelled 但 asyncio.Task 没被中断、名额仍被占」的撕裂，是已知遗留缺陷。

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

**provider 解析（resolution）**：
给定一个生成任务，决定它应使用哪个 **ProviderModel**。优先级自高而低：本次请求（payload）> 项目级（project.json）> 全局默认。这是"选身份"，不含 backend 构造。

**ProviderModel**：
provider 解析的结果——一对 `(provider_id, model_id)`（provider_id 为规范 id）。是"选了哪个 provider 及其 model"的值对象，**不是** backend（未构造任何客户端）。
_Avoid_: ResolvedBackend、BackendSelection（会与 backend 混淆）。

**capability（t2i / i2i）**：
图片任务的两种形态——t2i 文生图（无参考图）、i2i 图生图（带参考图）。一个镜头属于哪种，取决于"开画那一刻"是否拼出了参考图，**只有执行时才能确定**（见 `docs/adr/0001`）；入队与调度（worker claim）这两个执行前环节都无法获知。视频任务无 capability 维度。

## 示例对话

> **Dev**：worker 认领一个图片任务时，怎么知道用哪个 provider 限流？
> **Expert**：它做 provider 解析，但只到"选身份"为止——拿 provider 不拿 backend，更不真正生成。
> **Dev**：那它知道是 t2i 还是 i2i 吗？要是用户给两者配了不同 provider？
> **Expert**：不知道。capability 执行时才定，worker 只能按 t2i 取个代表性 provider 限流。真正用哪个，执行层会重新精确解析一次。
> **Dev**：那 project.json 里要是写着 `seedance` 呢？
> **Expert**：那是 legacy provider 名，迁移后不该再出现。系统只认规范 id `ark`。
