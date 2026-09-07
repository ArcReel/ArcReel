# 使用记录统一行：生成任务与供应商调用的关联调查

> 日期：2026-09-02。对应 [#2287](https://github.com/ArcReel/ArcReel/issues/2287)（地图 [#2286](https://github.com/ArcReel/ArcReel/issues/2286)）。
> 目标：使用记录以「一次调用」为行，进行中的生成任务（`tasks`）与已结束的供应商调用（`api_calls`）要合成同一条时间线。本文逐条回答工单的 5 个问题，每条结论附代码位置（行号以 main `1b6a0c2be` 为准），最后给出「统一行」的可行拼法与缺口清单。
> 调查范围：`lib/db/models/{api_call,task}.py`、`lib/db/repositories/{usage_repo,task_repo,base}.py`、`lib/ledger.py`、`lib/media_generator.py`、`lib/text_generator.py`、`lib/generation_worker.py`、`server/agent_runtime/session_manager.py`、`lib/custom_provider/endpoint_test/trial_run.py`、`server/routers/{usage,tasks}.py`、`server/auth.py`、前端 `GlobalHeader` / `UsageDrawer` / `TaskHud` / `StudioLayout` / `useTaskRefresh` / `usage-store` / `tasks-store`、ADR 0021 / 0053。未跑代码，全部为静态阅读结论；标注「未验证」处为推断。

## 结论速览

| 问题 | 一句话结论 |
|---|---|
| 1 回指 | `api_calls` 没有 `task_id` / `resource_id` / `provider_job_id` 列；唯一精确链是 **`tasks.payload_json.api_call_id` → `api_calls.id`**，且只有 video 路径写它。`segment_id` / `output_path` 只能做弱匹配。一个任务至多一条调用（重试下载与 resume 都复用原行），但无任务的调用大量存在。 |
| 2 来源 | 文本调用能用的只有 `project_name`（可能为空串）、`provider`、`model`、`prompt` 前 500 字；文本任务类型（剧本/概览/风格分析）、助手 session_id、试跑端点标识都没落库。 |
| 3 可见范围 | 两表都有 NOT NULL `user_id`（恒 `"default"`），读侧 `_scope_query` 是 no-op，`/usage/*` 与 `/tasks*` 路由不带用户参数；认证依赖把任何 token 都映射到 `DEFAULT_USER_ID`。多用户隔离按 ADR 0021 留给子类覆盖，两表口径一致。 |
| 4 演示回退 | `GlobalHeader.tsx:76-77` 把 demo 下 `usageProjectName` 置 null → 抽屉与顶栏费用退回**全局**用量；任务侧 `StudioLayout.tsx:70-72` + `useTaskRefresh.ts:44-54` 直接停用并清空 store。隐藏入口要动费用徽章、抽屉、任务雷达三处按钮及「已打开面板随 demo 切换关闭」的 effect。 |
| 5 读接口口径 | `/usage/calls`：page_size 默认 20 上限 100，按 `started_at desc`；`/projects/{name}/tasks`：默认 50 上限 500，按 `updated_at desc, queued_at desc`；`/tasks/stats` 无分页，按状态计数。三者都是 offset 分页、时间键不同、无次序 tiebreak（usage）。 |

## 1. `api_calls` 行能否回指 `tasks` 行

### 现状

**`api_calls` 表可用于关联的列**（`lib/db/models/api_call.py:14-50`）：`project_name`（L18）、`call_type`（L19）、`model`（L20）、`output_path`（L28，Text 可空）、`segment_id`（L29，String(20) 可空、有索引）、`started_at` / `finished_at`（L30-31）、`provider`（L36）、`last_provider_response`（L44，JSON）、`user_id`（`UserOwnedMixin`，`lib/db/base.py:33-42`）。**没有** `task_id`、`resource_id`、`provider_job_id`、`resource_type`、`task_type` 列。

**`tasks` 表**（`lib/db/models/task.py:13-69`）：`resource_id`（L21，NOT NULL）、`resource_type`（L24，仅 image_edit 写）、`payload_json`（L26）、`provider_id` / `provider_job_id`（L35-36）、`queued_at` / `started_at` / `finished_at` / `updated_at`（L47-50）。**没有** `api_call_id` 列。

逐个字段对得上的程度：

| 线索 | 能否对上 | 依据 |
|---|---|---|
| `tasks.payload_json["api_call_id"]` → `api_calls.id` | **精确，但只有 video / reference_video 任务写** | 写入点唯一在视频记账括号内：`lib/media_generator.py:1007-1014`（`task_id is not None` 时调 `persist_api_call_id`），落到 `lib/db/repositories/task_repo.py:1049-1056`（`_merge_payload_field` 写进 payload）。image 记账括号（`media_generator.py:613-624`）与 audio 记账括号（`media_generator.py:729-737`）都不写；image/tts 任务的 `task_id` 只用于 staging 路径（`media_generator.py:48-71`）。 |
| `tasks.resource_id` ↔ `api_calls.segment_id` | **弱匹配，仅白名单资源** | `segment_id_for`（`media_generator.py:141-154`）：image 只在 `storyboards/videos/grids`、video 只在 `storyboards/videos/reference_videos` 时把 `resource_id` 写进 `segment_id`；audio 无条件透传。角色/场景/道具/产品图的 `segment_id` 为 NULL（ADR 0053 `docs/adr/0053-cost-attribution-ledger-key-primary.md:7`）。且 ADR 0053:14 明确 ID 不全局唯一、不保证与所在集一致，同一 `resource_id` 会随重生成累积多条调用。 |
| `tasks.provider_job_id` ↔ `api_calls.?` | **对不上** | `api_calls` 无此列。`provider_job_id` 只在 tasks 侧由 `persist_provider_job_id` 写（`task_repo.py:966-992`）。`last_provider_response` 是任意 JSON 快照（`usage_repo.py:42-56` 限 64 KiB），里面可能含供应商 job id，但无结构化保证（未验证）。 |
| `api_calls.output_path` ↔ 任务产物 | **弱匹配，仅成功行** | `output_path` 只在成功结算时写（`lib/ledger.py:146` → `usage_repo.py:367-411` 的 `finish_call(output_path=...)`）；失败 / pending 行为 NULL。tasks 侧对应路径在 `result_json` 里（`task_repo.py:551` `mark_succeeded(result)`，结构未验证），无独立列。 |
| `project_name` + `call_type` + 时间窗 | **启发式** | 两表都有 `project_name`；`tasks.media_type`（`task.py:20`）≈ `api_calls.call_type`；`api_calls.started_at` 落在 `tasks.started_at..finished_at` 内。并发同类任务时会串。 |

**一个任务是否可能产生多条调用**：调查到的所有路径都复用同一条 `api_calls` 行：

- 重试下载：`task_repo.retry_artifact_download`（`task_repo.py:601-648`）把原 `ApiCall` 从 failed/pending 翻回 pending，注释明写「仍是同一条调用，不新增计费行」（L623-625）。
- resume（重启续跑 / 崩溃窗口）：`generate_video_resume_async` 不开新记账括号（`media_generator.py:1135-1138`），只经 `ledger.resume_success` / `resume_failed` 按 `call_id` 精准翻 pending（`lib/ledger.py:152-162`，`media_generator.py:1229-1239`）；派发前判死也是翻原行（`lib/generation_worker.py:1337-1355`）。
- `api_calls.retry_count` 列（`api_call.py:33`）没有任何写入点，只在 `_row_to_dict` 读出（`usage_repo.py:144`），恒为 0。
- 未验证：`grid` / `reference_video` 任务内部是否会对同一 task 连续发起多次 backend 调用（例如多单元参考生视频）；若有，也只有最后一次 video 调用的 id 会留在 `payload.api_call_id`（`_merge_payload_field` 覆盖写）。

**一条调用是否可能无任务**：大量存在。

- 全部文本调用：`TextGenerator.generate`（`lib/text_generator.py:51-66`）不接 task；调用点包括剧本生成（`lib/script_generator.py:262,1328`）、分集规划（`lib/episode_planner.py:412,659`）、项目概览（`lib/project_manager.py:3612-3625`）、风格分析（`server/routers/files.py:1023-1024`）、`server/text_generation.py:850,1512,1659`。
- 助手会话补录：`session_manager._record_assistant_usage` 走 `ledger.backfill`（`server/agent_runtime/session_manager.py:1184-1210`），一次写终态行。
- 自定义端点试跑：`trial_run.py:375-386`，`project_name=""`、`call_type="video"`，无任务。
- 记账括号任何调用点在 `task_id=None` 时都不留任务线索（`media_generator.py:487,532` 的 `task_id: str | None = None` 默认）。
- 反向也成立：image 任务在进程崩溃后被标 `restart_lost_image`（`generation_worker.py:1163-1173`），其 pending `ApiCall` 无 `api_call_id` 可翻，会永久留 pending（推断：记账括号只在 `CancelledError` / `Exception` 时结算，`ledger.py:133-150`；仓储层无 pending 清扫逻辑，grep `stale|sweep|orphan` 于 `usage_repo.py` / `ledger.py` 无命中）。

### 可行拼法

- **主链**：`tasks.payload_json.api_call_id` 是现存唯一精确外键，video 任务可直接拼；任务 API 已把 `payload` 原样返回（`task_repo.py:96-125` `_task_to_dict` 含 `payload`；`server/routers/tasks.py:74-112` `_localize_task` 只剥 `execution_checkpoint_json`），前端 `TaskItem.payload`（`frontend/src/types/task.ts:32`）可读到。
- **弱链兜底**：非 video 任务用 `(project_name, user_id, media_type≈call_type, segment_id==resource_id, started_at ∈ [task.started_at, task.finished_at])` 启发式；资产图（`segment_id` NULL）再退化到 `output_path` 前缀与 `resource_id` 文件名匹配（`usage_repo._classify_asset_output_path` 已按路径推断资产类型，`usage_repo.py:108-124`，可复用）。
- **统一行定义**：终态以 `api_calls` 行为准，进行中（queued/running/cancelling）以 `tasks` 行为准；有 `api_call_id` 的任务在其调用行结算后合并成一行，避免同一事双计。

### 缺口

1. `api_calls` 缺 `task_id` 列（可空、索引）；`ledger.record` 缺 `task_id` 入参，image / audio 记账括号不持久化 `api_call_id`。
2. `retry_count` 无写入，无法表达「同一任务第几次执行」；任务侧重试只刷新 `started_at`（`task_repo.py:634-639`）。
3. 无任务的 image pending 行没有回收路径。

## 2. 无任务文本调用可显示的「来源」字段

### 现状

写入口只有两条：`ledger.record`（`lib/ledger.py:95-111` 的入参集合）与 `ledger.backfill`（`ledger.py:169-207`）。文本行实际落库字段：

| 字段 | 值 | 依据 |
|---|---|---|
| `project_name` | 调用方传入，**可为空串** | `text_generator.py:58` `project_name or ""`；`trial_run.py:376` 固定 `""`；会话补录用 `managed.project_name`（`session_manager.py:1197`） |
| `call_type` | `"text"` | `text_generator.py:59`，`session_manager.py:1198` |
| `provider` / `model` | 解析层 provider_id 与 backend 模型 | `text_generator.py:60-61`；助手固定 `PROVIDER_ANTHROPIC`（`session_manager.py:1201`） |
| `prompt` | 前 500 字 | `text_generator.py:62`；`session_manager.py:1200` 取最后一条用户提示 |
| `status` / `error_message` / 时间 / token / 费用 | 结算写 | `usage_repo.py:367-411` |
| `segment_id` / `output_path` | NULL | 文本路径不传 |

文本任务类型 `TextTaskType`（SCRIPT / OVERVIEW / STYLE_ANALYSIS，见 `text_generator.py:44-49` 工厂）在创建 backend 时已知，但**没有传给 ledger**。助手会话的 `session_id` 在同一调用链上可得（`managed.session_id`，`session_manager.py:1179`），同样未落库。试跑无端点标识。

前端 `UsageCall` 类型（`frontend/src/stores/usage-store.ts:21-40`）只声明了 `project_name / call_type / model / status / cost / provider / output_path / resolution / duration / error / 时间 / token`，未声明 `prompt`、`segment_id`；抽屉行渲染（`frontend/src/components/layout/UsageDrawer.tsx:235,290,321`）显示产物文件名、模型、token、时间，不显示 `project_name`、`prompt`。

### 可行拼法

- 现有字段能拼出的「来源」：`provider == anthropic && call_type == text` → 助手会话；`project_name == ""` → 试跑或无项目文本调用；`prompt` 前缀可作悬停提示。这些都是推断，不能区分剧本生成与分集规划。
- 短期：把 `TextTaskType` 编进 `ledger.record` 的现有入参（例如 `prompt` 前缀标签）属于 hack，不推荐。

### 缺口

4. `api_calls` 缺「用途 / 来源」列（建议 `purpose` 或 `origin`，取值如 `script_generation` / `episode_planning` / `overview` / `style_analysis` / `assistant_session` / `endpoint_trial` / `task:<task_type>`），以及可空 `session_id`。`ledger.record` / `backfill` 需新增对应入参，5 处文本调用点与 `session_manager` / `trial_run` 补传。
5. 前端 `UsageCall` 与 `/usage/calls` 返回体已含 `segment_id` / `prompt`（`usage_repo.py:126-158`），前端类型未声明，需补。

## 3. 多用户模式下 `user_id` 与任务表的可见范围

### 现状

- 两表都通过 `UserOwnedMixin` 带 NOT NULL、`server_default="default"`、有索引的 `user_id`（`lib/db/base.py:33-42`；`api_call.py:14`；`task.py:13`）。
- 读侧过滤点是 `BaseRepository._scope_query`，开源版为 no-op（`lib/db/repositories/base.py:17-19`）。`UsageRepository.get_calls` 与 `get_stats*` 都套了它（`usage_repo.py:665,672`）；`TaskRepository.get / list_tasks / get_stats` 同样（`task_repo.py:1195,1224,1235,1252`）。
- 路由层不接用户参数：`/usage/calls`（`server/routers/usage.py:56-81`）与 `/tasks*`（`server/routers/tasks.py:114-165`）都没有 `CurrentUser` 依赖，也不向仓储传 `user_id`。
- 认证层无论 token 内容如何都返回 `DEFAULT_USER_ID`（`server/auth.py:69-73` `_anonymous_user`，`server/auth.py:426-432` `_payload_to_user`）。
- 写侧：video/image/audio 记账 `user_id` 取 `MediaGenerator._user_id`（`media_generator.py:173,622,736,1001`）；文本调用不传 `user_id`，走 `ledger.record` 默认 `DEFAULT_USER_ID`（`ledger.py:107`，`text_generator.py:57-63`）；助手补录 `getattr(self, "_user_id", DEFAULT_USER_ID)`（`session_manager.py:1202`）。任务入队 `user_id` 由调用方传（`lib/generation_queue.py:398`；`server/tool_runtime.py:254-265`），去重唯一索引含 `user_id`（`task.py:57-68`）。
- ADR 0021（`docs/adr/0021-multi-user-preembed-scope-query.md:7-12`）：商业版通过子类覆盖 `_scope_query` 注入过滤；`claim_next` 走原生 SQL 是已知例外。

### 可行拼法

统一行的可见范围可以完全交给 `_scope_query`：两表同一缝隙、同一 `user_id` 语义，合并查询只要经同一 `BaseRepository` 子类即可。不需要额外的按用户过滤参数。

### 缺口

6. 文本调用不传 `user_id`，商业版下会全部归到 `"default"`，与任务表按调用方用户落库的口径分叉（`text_generator.py:57-63` vs `generation_queue.py:398`）。统一行若跨表合并，文本行会在多用户下「消失」或错归。

## 4. 演示项目下 projectName 为 null 的回退逻辑与隐藏入口

### 现状

- **用量侧回退**：`frontend/src/components/layout/GlobalHeader.tsx:73-77`：`demoMode || isDemoProject(currentProjectName)` 时 `usageProjectName = null`（注释：演示项目在后端没有用量记录，按项目查会 404，退回全局用量）。该值喂给顶栏费用统计（L88-99 `API.getUsageStats(usageProjectName ? {...} : {})`）和 `UsageDrawer`（L339-344 `projectName={usageProjectName}`）。抽屉内 `projectName ?? undefined`（`UsageDrawer.tsx:65,80-83`）→ 请求不带 `project_name` → 后端返回**全局**调用列表（`usage.py:59` 可选参数，`usage_repo.py:431-432` 空值不加过滤）。
- **任务侧停用**（不是回退）：`frontend/src/components/layout/StudioLayout.tsx:66-72` 算 `isEffectivelyDemo`，`useTaskRefresh(sseProjectName, !isEffectivelyDemo)`；`frontend/src/hooks/useTaskRefresh.ts:44-54` 在 `enabled=false` 时清空 `refreshScope / tasks / stats / connected`。注释指出 `projectName=null` 语义是「不按项目过滤」而非「停用」（`useTaskRefresh.ts:28-30`，`tasks-store.ts:35-38`）。
- **演示判定**：`frontend/src/onboarding/demo-project.ts:34,45-47`（`DEMO_PROJECT_NAME = "onboarding_demo"`，大小写不敏感比较）；`useDemoWorkbench`（`GlobalHeader.tsx:9,76`）。
- **入口现状**：费用徽章按钮（`GlobalHeader.tsx:287-296`）与任务雷达按钮（`GlobalHeader.tsx:348-380`，`<TaskHud>` L380）在 demo 下**仍然渲染且可点击**；只有导出按钮按 `demoMode` 禁用（L398,408-409），并有「导出弹窗打开期间切入 demo 则关闭」的 effect（L79-85）。

### 可行拼法

统一行面板在 demo 下的策略应二选一并写死：(a) 与任务侧一致——隐藏入口并清空 store；(b) 与用量侧一致——退回全局。两侧现状矛盾（任务清空、用量退全局），合成一条时间线后会出现「demo 下看得到历史调用但看不到任务」。建议选 (a)。

### 缺口 / 需动的点

7. `GlobalHeader.tsx:287-296` 费用徽章、`339-344` 抽屉、`348-380` 任务雷达 + `TaskHud`：按 `demoMode || isDemoProject(currentProjectName)` 隐藏或禁用；仿照 L79-85 加一个 effect 在切入 demo 时关闭已打开的抽屉 / HUD。
8. `GlobalHeader.tsx:76-77` 的 null 回退在入口隐藏后可删，否则顶栏仍会发一次全局用量请求。
9. `TaskHud.tsx:609` 直接读 `useTasksStore().tasks`，不感知 demo；若保留渲染，需要在 HUD 内加空态。

## 5. 三个读接口的分页与排序口径

| 接口 | 分页参数与上限 | 排序 | 过滤 | 其它 |
|---|---|---|---|---|
| `GET /usage/calls`（`server/routers/usage.py:56-81`） | `page`≥1 默认 1；`page_size` 默认 **20**，1..**100**（L64-65） | `started_at DESC`，**无 tiebreak**（`usage_repo.py:671`） | `call_id / project_name / call_type / status / start_date / end_date`；日期按 UTC 整日落在 `started_at`（`usage_repo.py:439-444`）；`_build_filters` 支持 `provider` 但 `get_calls` 不透传（`usage_repo.py:643-660`） | offset 分页，返回 `{items,total,page,page_size}`（`usage_repo.py:670-682`）；repo 不 clamp，靠路由 `le=100` |
| `GET /projects/{name}/tasks`（`server/routers/tasks.py:144-165`） | `page`≥1 默认 1；`page_size` 默认 **50**，1..**500**（L151-152）；repo 再 clamp 1..500（`task_repo.py:1210-1211`） | `updated_at DESC, queued_at DESC`（`task_repo.py:1231`） | `status / task_type / source`；`project_name` 来自路径 | offset 分页，同形返回体（`task_repo.py:1239-1244`）；每项经 `_localize_task` 本地化错误文案（`tasks.py:74-112`），usage 无此步 |
| `GET /tasks/stats`（`server/routers/tasks.py:114-119`） | 无分页 | 无 | 可选 `project_name` | 按 `status` GROUP BY 计数并补零（`task_repo.py:1246-1274`），返回 `{stats:{queued,running,cancelling,succeeded,failed,cancelled,total}}` |

补充：前端任务 store 实际调的是全局 `GET /tasks`（`server/routers/tasks.py:121-141`，同口径）配 `TASKS_PAGE_SIZE = 200`（`frontend/src/stores/tasks-store.ts:8,250-252`），不是 `/projects/{name}/tasks`；用量抽屉用 `page_size` 20（`usage-store.ts:67`）。`/usage/stats`（`usage.py:19-53`）是用量侧的计数接口，与 `/tasks/stats` 维度不同（按供应商 / 类型 / 币种 vs 按任务状态）。

### 可行拼法

- 时间键：调用行用 `started_at`（`api_call.py:30`，NOT NULL），任务行用 `queued_at`（`task.py:47`，NOT NULL）；`tasks.updated_at` 会随状态变化跳动，不适合做时间线主键。
- 合并方式：单表 UNION 视图（`api_calls` 终态行 ∪ `tasks` 活跃行，排除 `payload.api_call_id` 已能对上调用行的任务）按 `(ts DESC, id DESC)` keyset 分页，一次查询出一页；或前端分别拉两页后客户端合并，但两接口 page_size 上限与总数语义不同（20/100 vs 50/500），翻页会错位。

### 缺口

10. 两接口无共同的次序 tiebreak，同一秒多条调用时 offset 翻页可能重复 / 丢行；统一行需要 keyset 游标（时间戳 + id）。
11. 三接口的 `project_name` 过滤语义一致，但 `status` 取值集合不同（usage：`pending/success/failed`；tasks：`queued/running/cancelling/succeeded/failed/cancelled`），统一行需要一张映射表。
12. tasks 路由做了错误文案本地化，usage 路由没做（`error_message` 原样返回，`usage_repo.py:138`），合并后同一列两种口径。

## 统一行的整体建议

**推荐拼法**：新增服务端时间线读接口（例如 `GET /usage/timeline`），由一个 `UsageRepository` 子方法完成：

1. 取 `api_calls` 全部行作为「已结束 / 进行中调用」的主行（含 pending）；
2. 取 `tasks` 中 `status IN (queued, running, cancelling)` 且**没有**可对上调用行的任务作为「排队中」补行（video 任务用 `payload.api_call_id` 判定，其余任务用第 1 节的弱链启发式）；
3. 按 `(ts, id)` keyset 分页，`ts` 取 `api_calls.started_at` / `tasks.queued_at`；
4. 经同一 `_scope_query` 做多用户过滤；
5. 每行带 `origin`（来源）、`task_id`（可空）、映射后的统一 `status`。

**需先补的缺口**（按优先级）：

| # | 缺口 | 建议改动 |
|---|---|---|
| 1 | `api_calls` 无 `task_id`；image / audio 不持久化 `api_call_id` | 加可空索引列 `api_calls.task_id`，`ledger.record` 增 `task_id` 入参并在三条媒体记账括号透传；迁移时从 `tasks.payload_json.api_call_id` 反向回填 |
| 4 | 文本 / 试跑 / 助手调用无来源字段 | 加 `api_calls.purpose`（枚举字符串）与可空 `session_id`；`TextGenerator.create` 已知 `TextTaskType`，顺手带进 `generate` 的记账 |
| 6 | 文本调用 `user_id` 恒 default | `TextGenerator` 接 `user_id` 并透传到 `ledger.record` |
| 10/11/12 | 分页 tiebreak、状态映射、本地化口径 | 新接口内统一处理；不改现有三个接口 |
| 7/8/9 | demo 入口 | 前端按第 4 节清单处理 |
| 2/3 | `retry_count` 无写入、image 孤儿 pending 无回收 | 可后置；统一行先按「pending 超过 N 小时显示为未知」兜底（口径未定，需产品决策） |
