# `sdk_tools` 宿主耦合与长任务现状

> 口径：ArcReel `main`，提交 `2b057ae7052c569c08d20b775cd436749c2b76b6`（2026-08-20）。本文只描述这一版本的当前事实，并以仓库源码和实现提交为依据。

## 结论

`server/agent_runtime/sdk_tools/` 目前是 **18 个文件、16 个工具模块、32 个已注册工具**，而不是 13 个文件 / 26 个工具。工具面已经吸收了工作流计划、资产盘点完成态、step1 重建完成态、事务式剧本编辑、资产改名和项目迁移恢复等能力；生成工具也已经统一成逐 ID、可机读的 `GenerationBatchResult`。目录清单与注册顺序见 `server/agent_runtime/sdk_tools/__init__.py:23-108,161-205`。

但“宿主无关 handler”尚未存在。32 个工具仍全部是 `xxx_tool(ctx)` 工厂，工厂内直接使用 `claude_agent_sdk.tool`，由每会话 `create_sdk_mcp_server` 注册；项目作用域仍通过 `ToolContext` 闭包绑定，而地图已定的远程 MCP 目标是每次调用显式携带 `project`。当前源码把项目、项目根目录、`ProjectManager`、队列单例、数据库 session factory、供应商配置、同机文件与锁，以及 SDK 返回信封混在 adapter 内。`ToolContext` 与装配路径见 `server/agent_runtime/sdk_tools/_context.py:21-37`、`server/agent_runtime/options_assembler.py:263-297`。

Spec #1669 的实现显著改善了“可抽取性”，但没有完成抽取：

- 权威制作计划已经是独立服务，SDK 文件只是薄 adapter（`server/agent_runtime/sdk_tools/workflow_plan.py:23-56`；实现提交 `7b1f7cbae311558a85879feffa1e08f923c9f060`）。
- 生成结果已经是 MCP / REST 共享的版本化领域模型，且强制 `requested = succeeded ∪ failed ∪ blocked`（`lib/generation_result.py:294-339`；实现提交 `6adc823af6af4ccf4f5216f99a336f3d6497a7a3`）。
- 正式剧本写入已有共享事务服务，SDK 工具只是命令适配（`server/agent_runtime/sdk_tools/patch_script.py:120-136,139-231`；实现提交 `a280775462e90cd6fcf0cbfd2a93baafd7c2bec9`）。
- 产物清单、时效判断、迁移失败阻断和恢复入口已经成为共享领域能力（`server/agent_runtime/sdk_tools/__init__.py:110-158`、`server/agent_runtime/sdk_tools/retry_project_migration.py:17-63`；实现提交 `e61feae4d49c0372165c0684c459600e310dd4a5`、`4ae96b22c20c5d89a963b39f1d339f4ecab1651c`）。
- 这些 machine payload 在当前内嵌 SDK 链路里**没有真正传到 MCP client**：锁定的 `claude-agent-sdk 0.2.139` 只读取 handler dict 的 `content` 和 `is_error`，构造 `CallToolResult` 时丢弃 `generation_result` / `problem` / `workflow_plan` / `batch_admission` 等额外键；`mcp 1.29.0` 的正规机器结果槽是 `structuredContent`（版本见 `uv.lock:636-641,1662-1663`；运行时实现见 `.venv/lib/python3.12/site-packages/claude_agent_sdk/__init__.py:459-526`、`.venv/lib/python3.12/site-packages/mcp/types.py:1363-1369`）。远程 adapter 必须显式映射，不能复用当前 dict 后假定它会透传。

长任务结论没有改变：**面向智能体的 9 个生成入队工具仍无一个“入队即返”**。8 个工具走 `batch_enqueue_and_wait`，`generate_grid` 自己逐组入队后仍并行等待全部任务。等待默认每 1 秒轮询、3600 秒超时；超时或 worker 离线只中断等待，不取消已创建任务。与旧形态相比，当前代码已经把这种情况明确记作 `interrupted`，并逐 ID 返回 task / artifact / provider submission 三条轴，避免把仍可能落地的付费任务误报为普通失败（`lib/generation_queue.py:269,656-657`、`lib/generation_queue_client.py:44-57,79-121,618-694`）。

因此，远程 MCP 不应直接复刻当前 1 小时阻塞 handler。最小稳健边界是：**同步预检与整批准入 → 入队即返批次 / task handles → 独立查询 / 取消工具**；同时把 5 个不经队列的文本生成 / 分集规划长调用任务化，否则远程连接断开后仍没有可恢复的任务身份。

## 1. 当前工具面

注册表是唯一目录真相源，并由前端三语显示名测试约束（`server/agent_runtime/sdk_tools/__init__.py:68-108`、`tests/test_frontend_mcp_tool_i18n.py`）。

| 模块 | 已注册工具 |
|---|---|
| `asset_inventory.py` | `complete_asset_inventory` |
| `workflow_status.py` | `complete_step1_rebuild` |
| `workflow_plan.py` | `get_workflow_plan` |
| `enqueue_assets.py` | `list_pending_assets`, `generate_assets` |
| `enqueue_storyboards.py` | `generate_storyboards` |
| `enqueue_image_edits.py` | `edit_images` |
| `enqueue_grid.py` | `generate_grid` |
| `enqueue_videos.py` | `generate_video_episode`, `generate_video_scene`, `generate_video_all`, `generate_video_selected` |
| `enqueue_narration_audio.py` | `generate_narration_audio` |
| `text_generation.py` | `generate_episode_script`, `confirm_script_review`, `normalize_drama_script`, `split_reference_video_units`, `open_step1_for_edit`, `validate_and_promote_draft`, `split_narration_segments`, `get_video_capabilities` |
| `episode_planning.py` | `plan_episodes`, `reset_episode_planning` |
| `patch_script.py` | `get_episode_script_revision`, `patch_episode_script`, `insert_segment`, `remove_segment`, `split_segment` |
| `patch_episode_meta.py` | `patch_episode_meta` |
| `patch_project.py` | `patch_project` |
| `rename_asset.py` | `rename_asset` |
| `retry_project_migration.py` | `retry_project_migration` |

`_context.py` 和 `__init__.py` 是基础设施文件，不注册自身工具。16 个工具模块里的 32 个工厂都接受 `ToolContext`；项目名没有出现在任何工具 schema 中。

## 2. 宿主耦合面

### 2.1 SDK 注册、会话与项目作用域

当前装配是每个内嵌智能体会话创建一个进程内 MCP server：

1. `OptionsAssembler.build(project_name, ...)` 解析项目 cwd；
2. 调用 `build_arcreel_mcp_server(project_name, projects_root)`；
3. 后者创建 `ToolContext`，逐个调用 32 个工具工厂；
4. 用 `create_sdk_mcp_server(name="arcreel", ...)` 注册；
5. 注入 `ClaudeAgentOptions.mcp_servers`。

来源：`server/agent_runtime/options_assembler.py:194-220,263-302`、`server/agent_runtime/sdk_tools/__init__.py:161-205`。

这个闭包有一条重要安全不变量：agent 不能在参数里改项目；项目由宿主会话选定（`server/agent_runtime/sdk_tools/_context.py:21-37`）。远程 MCP 的既定接口却是 stateless、每次显式 `project`，所以不能只是换装饰器：远程 adapter 必须先做身份授权、项目名规范化和项目根 containment，再构造与闭包等价的 `ProjectScope`。内嵌 adapter 仍可从会话注入同一个 scope，以保留现有防跨项目注入属性。

`SessionActor` 对一个 SDK client 的 query 串行 drain；新 query 在当前响应结束前只能暂存一个，再多会以 session busy 拒绝（`server/agent_runtime/session_actor.py:94-118,149-181`）。因此当前阻塞生成工具占用的不只是一个 handler 协程，也占住该会话的有效工作回合。

### 2.2 依赖不是一个 `ctx`，而是一组本机服务

`ToolContext` 自身只保存 `project_name`、`projects_root` 和 `ProjectManager`，但 handler 经导入继续触达以下依赖：

| 依赖 | 当前用法 | 远程化含义 |
|---|---|---|
| `ProjectManager` | 读写 `project.json`、剧本、资产、项目路径；事务工具也以它为入口 | 应注入为项目仓储 / 领域服务，不能让远程 adapter 自己拼路径 |
| 项目本机文件系统 | step1、隔离草稿、批次 checkpoint、产物清单、媒体路径、版本 | 远程 server 与 ArcReel 同进程部署时可保留；外部 agent 不应被要求共享这套文件系统 |
| 本机文件锁 | `locked_script`、正式 step1 lock、草稿晋升 lock 等 | 锁必须留在 ArcReel 服务端；不能把读改写拆到客户端 |
| `get_generation_queue()` | 入队、活动任务检查、轮询任务行 | 抽成 queue service；remote handler 不应直接依赖模块单例 |
| `async_session_factory` / `ConfigResolver` | 供应商能力、配置和任务状态解析 | 抽成 capability / config service，并显式传调用身份 |
| `VersionManager` / Artifact Manifest | 判断 current / stale / missing / blocked、选择缺失项、版本恢复 | 继续作为领域服务；不要在 adapter 重建判断规则 |
| `workflow_planner` / migration verdict | 权威下一动作、项目级阻断 | 已是可复用 seam，应成为两种 adapter 的共同入口 |

直接证据包括 `server/agent_runtime/sdk_tools/_context.py:12-16,110-122`、`lib/generation_queue_client.py:17-22,74-121,175-218`、`server/agent_runtime/sdk_tools/text_generation.py:1015,1398,1598,1622`、`server/agent_runtime/sdk_tools/enqueue_videos.py:841-852,990-1061`。

当前队列客户端的 `user_id` 默认仍是 `DEFAULT_USER_ID`，任务来源也默认是 `source="skill"`；SDK 生成调用没有传会话属主或外部调用来源（`lib/generation_queue_client.py:124-154,175-217,365-381,508-515,596-615`）。远程 API Key 对应的调用身份若不进入 queue service，任务、费用和并发仍会归内部默认用户，审计也无法区分内嵌与外部 agent；这是外部接入前的必改项，不是可延后的细粒度授权增强。

### 2.3 返回信封与领域结果

基础错误仍返回 SDK 风格 dict：`{"content": [{"type": "text", ...}], "is_error": true}`（`server/agent_runtime/sdk_tools/_context.py:40-45,88-89`）。Spec #1669 又在同一顶层加入了仓库自定义字段：

- 生成工具：`generation_result`（`server/agent_runtime/sdk_tools/_context.py:64-80`）；
- 迁移错误：`problem`（`server/agent_runtime/sdk_tools/_context.py:47-61`）；
- 迁移恢复成功：`workflow_plan`（`server/agent_runtime/sdk_tools/retry_project_migration.py:31-40`）。

这些 payload 的**领域模型**适合复用，尤其 `GenerationBatchResult` 已明确声明供 REST / MCP adapter 原样序列化（`lib/generation_result.py:294-307`）；但当前 dict 信封本身不是一个已抽出的 transport-neutral 类型。更重要的是，当前锁定的 `claude-agent-sdk 0.2.139` 在 `create_sdk_mcp_server` 内只把 `content` 和 `is_error` 转成 `CallToolResult`，其余顶层键全部丢弃（`.venv/lib/python3.12/site-packages/claude_agent_sdk/__init__.py:459-526`）。`mcp 1.29.0` 为机器结果提供的标准槽是 `structuredContent`（`.venv/lib/python3.12/site-packages/mcp/types.py:1363-1369`）。因此目前所谓“可机读”只存在于 handler 内部 Python 返回值，不存在于实际内嵌 MCP 传输结果；抽取时应让核心 handler 返回 typed domain result，再由两个 adapter 显式编码 `content`、错误态与 `structuredContent`。

工具正文仍大量返回中文 agent-facing 文本与项目路径；每会话 `locale` 只进入 system prompt，没有进入 `ToolContext`（`server/agent_runtime/options_assembler.py:194-202,283-287`）。远程工具若保留结构化 payload，可以让客户端不解析中文；面向人展示的文本则需显式 locale，或明确继续采用单一 agent-facing 语言。

### 2.4 权限、hook 与跨工具闸门

所有 ArcReel MCP 工具通过 `mcp__arcreel__*` 加入 `allowed_tools`（`server/agent_runtime/options_assembler.py:266-281`）。PreToolUse 文件访问 hook 虽 matcher 为 `None`，但只处理内建 `PATH_TOOLS`，其余工具直接 continue（`server/agent_runtime/options_assembler.py:338-381`）；Write/Edit JSON hooks 也只匹配对应内建工具。当前业务确认并不依赖 `can_use_tool` 或 SDK hook。

真正的公共闸门在工具协议和注册包装层：

- **项目迁移失败**：注册时统一包装 20 个生成 / 正式写入工具；每次调用先读取 migration verdict，失败则返回结构化 problem（`server/agent_runtime/sdk_tools/__init__.py:110-158,198-204`）。远程 registry 必须复用同一 blocked set 或把这条规则下沉到公共 dispatch，不能复制后漂移。
- **视频请求档位确认**：四个视频工具接受全批单档 `confirmed_request_duration_seconds` 或逐 unit `confirmed_request_durations`；确认精确绑定本次秒数投影，正文、引用、供应商或 TTS 改变导致档位变化时需重确认（`server/agent_runtime/sdk_tools/enqueue_videos.py:85-121,295-314,344-378`）。
- **旁白交付选择**：四个视频工具强制 `narration_delivery = post_production | use_tts`，它是本次请求参数而非项目设置（`server/agent_runtime/sdk_tools/enqueue_videos.py:103-121,159-203`；实现提交 `7ab47f42ca52fdaa3cc9fd2678b31223a24dc56b`）。
- **整批视频准入**：任一 unit 有问题时零任务入队；Web 与 Agent 共用服务（`server/agent_runtime/sdk_tools/enqueue_videos.py:822-839,899-918`、`server/services/video_batch_admission.py:1-12`；实现提交 `d76443e2816f89c3111bff10ce53b021ab9b9d71`）。
- **分集规划重置确认**：波及已消费集时返回清单，`confirm_consumed=true` 后才执行（`server/agent_runtime/sdk_tools/episode_planning.py:146-207`）。
- **step1 审核确认**：`confirm_script_review` 调用共享 `ScriptReviewService`，描述层要求用户已明确同意（`server/agent_runtime/sdk_tools/text_generation.py:417-455`）。

前三类确认可随 typed 请求移植；最后一类仍有“调用者是否真的获得用户同意”的信任问题。远程 API 不能把“能调用工具”自动等同于“已获授权”，Spec 应明确哪些 API Key / agent autonomy 可以满足普通审核、哪些计费或破坏性确认必须携带独立授权凭据或用户确认记录。

### 2.5 文件系统混合工作流仍存在

Spec #1669 已把正式 project / script / step1 写入收归到事务工具，并增加 `open_step1_for_edit` + `validate_and_promote_draft`。但这两个工具仍把隔离草稿放在项目目录，再预期内嵌 agent 用文件工具编辑其中 `content`；例如 `generate_episode_script` 直接把隔离草稿路径和修改指示返回给 agent（`server/agent_runtime/sdk_tools/text_generation.py:341-364`），正式写回则在服务端持锁晋升（`server/agent_runtime/sdk_tools/text_generation.py:977-1040,1334-1476`）。

这条设计在内嵌宿主里安全，因为 agent cwd、文件权限 hook 和 ArcReel 项目盘共享同一视角；外部 agent 只拿远程 MCP 时不可达。远程工具面至少需要“读取隔离草稿 + revision-checked patch / promote”的结构化通道，或让 `open_step1_for_edit` 直接返回草稿内容与 revision、让 `validate_and_promote_draft` 接受 patch。仅把项目路径放进返回文本不能解决这一耦合。

## 3. 生成队列的长任务事实

### 3.1 队列客户端

`enqueue_task_only` 在创建任务前检查 worker lease；离线时不入队。任务创建走模块级 `get_generation_queue()`，默认任务所有者为 `DEFAULT_USER_ID`（`lib/generation_queue_client.py:175-218`）。

`wait_for_task` 每 1 秒读取任务行，直到 `succeeded / failed / cancelled`；默认等待 3600 秒，worker 连续离线宽限为 `max(20s, 2 × 10s lease TTL) = 20s`（`lib/generation_queue_client.py:56-121`、`lib/generation_queue.py:269,656-657`）。超时或离线异常不调用 cancel；任务仍可能在 worker 中提交并落地产物。

`batch_enqueue_and_wait` 分两阶段：先顺序入队以解析依赖，再 `asyncio.gather` 并行等待。入队途中失败时，已经创建的付费任务继续运行；未入队目标逐 ID 记账。等待超时 / worker 离线返回 `status="interrupted"`，不会伪装成 provider failed（`lib/generation_queue_client.py:508-593,618-694`；修复提交 `f37000db03a4b9b4afb7ef00bf2a2df14ac626ea`）。库里已经存在 `batch_enqueue_only`，但当前 9 个 SDK 生成工具没有采用它（`lib/generation_queue_client.py:596-615`）。

### 3.2 逐工具语义

| 工具 | 当前提交 / 等待语义 | 可恢复与结果语义 |
|---|---|---|
| `generate_assets` | 按资产类型构造 `TaskSpec`，每类 `batch_enqueue_and_wait` | 缺失项默认选择；显式名称可强制重做；按逐 ID `GenerationBatchResult` 返回（`server/agent_runtime/sdk_tools/enqueue_assets.py:165-278`） |
| `generate_storyboards` | 依赖链 TaskSpec 顺序入队、并行等终态 | 缺失项默认选择；失败记录与逐 ID 结果并存（`server/agent_runtime/sdk_tools/enqueue_storyboards.py:97-233`） |
| `edit_images` | i2i 能力整批预检后 `batch_enqueue_and_wait` | 只支持显式目标；编辑结果不写 Manifest，产物时效轴留空（`server/agent_runtime/sdk_tools/enqueue_image_edits.py:178-326`） |
| `generate_grid` | 逐组 `enqueue_task_only`，随后对全部 task `wait_for_task` | 联合图成功后仍同步切分，并按 scene 逐项报告；切分失败不建议重生以免重复计费（`server/agent_runtime/sdk_tools/enqueue_grid.py:376-533`） |
| `generate_narration_audio` | `batch_enqueue_and_wait` | 默认只补缺失 narrator 单元；worker 开始时重读最新剧本内容（`server/agent_runtime/sdk_tools/enqueue_narration_audio.py:115-222`） |
| `generate_video_episode` | 整批准入后 `batch_enqueue_and_wait(stop_on_failure=True)` | missing-only；批次 checkpoint 支持 `resume=true`，逐成功落盘，整批无失败才清除（`server/agent_runtime/sdk_tools/enqueue_videos.py:768-798,1429-1544`） |
| `generate_video_scene` | 单目标仍通过统一 batch path 入队并等待 | 显式强制重做；无批次 checkpoint（`server/agent_runtime/sdk_tools/enqueue_videos.py:1546-1624`） |
| `generate_video_all` | 统一 batch path 入队并等待 | missing-only；无批次 checkpoint（`server/agent_runtime/sdk_tools/enqueue_videos.py:1626-1712`） |
| `generate_video_selected` | 统一 batch path 入队并等待 | storyboard 路线按规范 ID 集合哈希 checkpoint；reference 路线显式重做不续传（`server/agent_runtime/sdk_tools/enqueue_videos.py:1714-1846`） |

视频的本地“批次进度 checkpoint”与队列持久化的 provider execution checkpoint 是两套机制：前者帮助一次 agent 调用续传目标集合；后者让已入队视频任务在进程重启后恢复 provider submission 身份（`server/agent_runtime/sdk_tools/enqueue_videos.py:832-839`；实现提交 `643d996707846f454189f593b48af344438f88c6`）。远程异步契约应保留后者，并重新评估前者是否还能作为 MCP 参数暴露；有持久批次记录后，客户端不应靠项目目录里的隐藏 JSON 认领任务集合。

### 3.3 不经队列的长调用

以下工具直接在 MCP handler 内 await 文本模型 / 规划器，没有 task row、task id、跨连接查询或取消通道：

- `generate_episode_script`：直接 `ScriptGenerator.create(...).generate(...)`（`server/agent_runtime/sdk_tools/text_generation.py:317-407`）；
- `normalize_drama_script`：直接 `TextGenerator.generate(...)`，随后持锁写正式 step1（`server/agent_runtime/sdk_tools/text_generation.py:496-609`）；
- `split_reference_video_units`：直接 `TextGenerator.generate(...)` 并执行隔离 / 正式写入（`server/agent_runtime/sdk_tools/text_generation.py:1479-1635`）；
- `split_narration_segments`：直接 `TextGenerator.generate(...)`（`server/agent_runtime/sdk_tools/text_generation.py:1817-1920`）；
- `plan_episodes`：直接创建并运行 `EpisodePlanner`（`server/agent_runtime/sdk_tools/episode_planning.py:90-143`）。

这些调用比媒体生成队列更难远程化：连接断开后服务端没有可供客户端重新定位的 durable job，也无法区分“模型仍在跑”“写入已完成但响应丢失”“调用从未开始”。它们应与媒体生成统一进入任务系统，或单独建立具备幂等 key、状态查询和取消语义的 durable operation 表。

### 3.4 查询与事件面

现有 HTTP 读面包括全局 / 项目任务列表、任务详情和统计；写面包括单任务与项目批量取消（`server/routers/tasks.py:114-214`）。SDK 工具不消费这些 HTTP 端点，而是在进程内直接轮询 queue。

前端的终态主通道是项目事件 SSE `/projects/{name}/events/stream`，queued → running 等中间态仍靠 3 秒轮询，空闲时退到 30 秒（`frontend/src/hooks/useTaskRefresh.ts:9-26,70-90`、`server/routers/project_events.py:52-77`）。远程 MCP 可以先复用任务 HTTP 读面，但它目前只表达单 task，不表达一次工具批次的完整 `requested / skipped / blocked / task_ids`。若生成工具改成入队即返，应新增 durable batch/submission read model，避免外部 agent 自行把多个 task row 猜回一次请求。

## 4. 同源抽取的建议边界

### 4.1 分三层，而不是复制 32 个工具

1. **领域 / 应用层**：保留现有 `workflow_planner`、`ScriptBatchEditor`、artifact currency、batch admission、generation result、migration verdict 等共享服务；把仍写在 SDK handler 内的目标选择与 orchestration 逐步下沉为普通 Python use case。
2. **宿主无关工具层**：定义 `ToolRequest + ProjectScope + CallerContext + Services -> ToolOutcome`。这里负责 schema 校验、领域调用和 typed result，不导入 `claude_agent_sdk`，不制造 `content` dict，也不从全局取 user。
3. **两个薄 adapter**：内嵌 adapter 从会话闭包取得 `ProjectScope`；远程 adapter 从显式 `project` + 已认证 API identity 构造 scope。两者分别做工具声明、协议错误映射、structured payload 和 locale 渲染。

现有 `workflow_plan.py` 和 `patch_script.py` 最接近目标形态；`enqueue_videos.py`、`text_generation.py` 仍同时承担 schema、领域编排、文件 I/O、供应商调用、确认呈现和 transport encoding，是优先拆分对象。

### 4.2 必须保住的不变量

- 项目根 containment 与 symlink / path traversal 防护必须发生在服务端；显式 `project` 不是任意路径。
- 所有正式 project / script / step1 写入继续走共享锁内事务，外部 agent 不获得裸写正式文件能力。
- migration failure 的工具级阻断、视频整批零入队准入、精确秒数确认、旁白交付选择和逐 ID 穷尽结果不能因换宿主退化。
- missing-only 不重生 stale / current；显式 ID 才允许强制重做。任务结果与产物时效继续分轴。
- 已入队任务在等待中断后不得被自动重试；返回 durable task / batch identity，并指示查询现有任务。
- API identity 必须下传至 queue `user_id` 和费用归因，不得继续落 `DEFAULT_USER_ID`。

### 4.3 远程长任务最小工具面

建议把当前 9 个生成工具拆成“准入并提交”语义，直接返回：

- `batch_id`；
- `requested / skipped / blocked`；
- 已创建的 `task_ids` 与 unit 映射；
- 每个阻断的稳定 `code / action / params`；
- 是否需要档位确认及应回传的精确秒数；
- provider submission 尚未发生时明确标注，而不是称为已生成。

另提供 `get_generation_batch(batch_id)` 和受控 `cancel_generation_batch(batch_id)`；服务端在终态重读 Artifact Manifest 后生成最终 `GenerationBatchResult`。MCP progress notification 可以作为体验增强，但不能成为唯一状态真相源。

5 个文本 / 规划长调用也返回 durable operation id；短小的只读 / 事务编辑工具继续同步完成。这样远程 MCP 的可靠性不再取决于单次连接能否维持 3600 秒，内嵌 SDK 也可选择继续包装成“提交后等待”的兼容体验。

## 5. 对地图后续决策的直接输入

### 工具面清单票应采用的事实

- 基线是 32 个工具，不是 26 个。
- Spec #1669 已提供可复用的制作计划、逐 ID 生成结果、事务式剧本编辑、产物状态和迁移恢复 seam；远程面应直接投影这些领域模型。
- “换 SDK 装饰器即可”不成立。所有工厂仍绑定 `ToolContext`，而且 registry 上还有 migration dispatch policy；需要明确的宿主无关 use-case 层和 caller/project scope。
- 外部工具 schema 必须加显式 `project`，但项目授权、containment、user / cost attribution 必须由远程 adapter 注入，不能交给 handler 自行信任字符串。
- 隔离草稿读改仍是“工具 + 共享文件系统”混合面，必须新增远程结构化草稿通道。
- `GenerationBatchResult`、`WorkflowPlan` 等 machine payload 应显式写入远程 MCP `structuredContent`，不应复用当前会被 SDK 丢弃的自定义顶层键，也不应让 agent 解析中文 `content`。

### 异步语义票应采用的事实

- 9 个生成工具全部等待终态；当前 repo 已有 `batch_enqueue_only`，但 SDK 工具未使用。
- 超时 / worker 离线的语义已明确为 `interrupted`，已入队任务继续执行，不能安全盲重试。
- 当前批量入队可能部分成功，且已逐 ID 区分“已创建任务”与“从未入队”；远程 batch contract 必须保留。
- 视频已具备 provider execution checkpoint；本地 batch checkpoint 是另一层，应由 durable batch read model 取代或收编。
- 5 个文本 / 规划长调用没有 durable task identity，是异步设计必须覆盖的同级问题。
- HTTP task 查询和项目终态 SSE 可复用，但还缺“单次工具批次”的持久聚合状态。

### 地图 fog 建议

现有 fog 的“外部 agent 用量 / 费用归因”不应整体留在雾中：`CallerContext → queue.user_id → cost attribution` 已经是远程工具正确性的前置决策，应毕业为一张明确票。API Key 的 per-project / read-only 细粒度仍可留雾区。

“项目事件流对外”也已可精确表述：在 durable batch polling 为状态真相源的前提下，是否额外以 MCP progress / 项目事件订阅做低延迟通知。应毕业进异步语义票或成为其阻塞后的后续票。

此外应新增一张可立即陈述的决策票：**远程隔离草稿如何读改与 revision-check**。这不是泛化的文件工具问题，而是当前正式 step1 修复 / 编辑流程的硬阻塞；其答案会决定 `open_step1_for_edit` 与 `validate_and_promote_draft` 的远程 schema。
