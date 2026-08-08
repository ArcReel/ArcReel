# sdk_tools 宿主耦合点盘点与长任务事实

> Research for issue #1704（地图 #1702）。目标：为「抽出宿主无关 handler + 远程 MCP 薄壳」列出适配面清单与各 `enqueue_*` 工具的任务语义现状。全部结论来自源码通读：`server/agent_runtime/sdk_tools/`（13 个文件）、`server/agent_runtime/options_assembler.py`、`lib/generation_queue_client.py`、`lib/generation_queue.py`、`server/routers/tasks.py`。

## 0. 盘点口径

`sdk_tools/` 目录共 13 个文件：11 个工具模块 + `_context.py`（共享上下文/helper）+ `__init__.py`（注册表）。注册的**工具总数是 26 个**（`ARCREEL_MCP_TOOL_IDS`，`__init__.py`），"13 个工具 handler" 实为 13 个文件。分组如下：

| 模块 | 工具 |
|---|---|
| `enqueue_assets.py` | `list_pending_assets`、`generate_assets` |
| `enqueue_grid.py` | `generate_grid` |
| `enqueue_image_edits.py` | `edit_images` |
| `enqueue_narration_audio.py` | `generate_narration_audio` |
| `enqueue_storyboards.py` | `generate_storyboards` |
| `enqueue_videos.py` | `generate_video_episode` / `generate_video_scene` / `generate_video_all` / `generate_video_selected` |
| `episode_planning.py` | `plan_episodes`、`reset_episode_planning` |
| `patch_episode_meta.py` | `patch_episode_meta` |
| `patch_project.py` | `patch_project` |
| `patch_script.py` | `patch_episode_script`、`insert_segment`、`remove_segment`、`split_segment` |
| `text_generation.py` | `generate_episode_script`、`confirm_script_review`、`normalize_drama_script`、`split_reference_video_units`、`open_reference_step1_for_edit`、`validate_and_promote_reference_draft`、`split_narration_segments`、`get_video_capabilities` |

## 1. 装配方式（宿主侧）

来源：`server/agent_runtime/options_assembler.py::OptionsAssembler.build` 与 `sdk_tools/__init__.py::build_arcreel_mcp_server`。

- **每会话一个进程内 MCP server**：`build(project_name, ...)` 内调用 `build_arcreel_mcp_server(project_name=..., projects_root=...)`，用 `claude_agent_sdk.create_sdk_mcp_server(name="arcreel", tools=[...26 个...])` 构造，经 `ClaudeAgentOptions(mcp_servers={"arcreel": ...})` 注入。handler 跑在 **server 主进程**（不进 agent 沙箱），因此能读 `projects/.arcreel.db`、直连供应商 HTTP（`__init__.py` 模块 docstring 明言这是刻意设计——绕过 `filesystem.denyRead` / 网络白名单）。
- **权限面**：`allowed_tools` 追加通配符 `mcp__arcreel__*`——所有 arcreel 工具**预放行**，权限链在 allow 规则（第 4 步）即通过，`can_use_tool`（第 5 步）不参与。`options_assembler.py` 里的各 hook（file-access、bash env scrub、JSON 校验）matcher 分别是 `None`/`Bash`/`Write|Edit`：matcher=None 的 hook 对 MCP 工具也会触发，但其实现只检查 `policy.PATH_TOOLS`（Read/Glob/Grep 等内建工具），对 MCP 工具是 no-op。**结论：26 个 MCP 工具与 SDK hook/permission 机制零交互，确认闸门全部落在工具协议层（见 §3）。**
- **工具名展示**：`ARCREEL_MCP_TOOL_IDS` 是短名单一真相源，前端 `tool_name_<id>` 三语 key 由 `tests/test_frontend_mcp_tool_i18n.py` CI 交叉校验——新增工具不补 i18n 会 fail CI。这是一条注册表→前端的耦合边。

## 2. 逐工具耦合点

### 2.1 `ToolContext` 闭包绑定（26/26 全量依赖）

`_context.py::ToolContext` 持 `project_name` / `projects_root` / `pm: ProjectManager`。**每个工具工厂都是 `xxx_tool(ctx)` 形态**，`project_name` 在会话装配时闭包封死——docstring 明言安全属性：*agent 无法通过 prompt 注入把工具重定向到别的项目*。工具入参层面 agent 只提供文件名/资源 ID，`validate_script_filename` 拒绝一切路径分隔符，项目根由 ctx 承载。

逐工具对 ctx 的实际用面：

| 用面 | 工具 |
|---|---|
| `ctx.pm`（load_project / load_script / locked_script / update_project / upsert_assets / get_pending_*） | 除 `plan_episodes`、`reset_episode_planning` 外全部 |
| `ctx.project_path`（直接文件 I/O：checkpoint、drafts、隔离草稿、failures.json） | `generate_grid`、`generate_storyboards`、4 个 `generate_video_*`、`plan_episodes`、`reset_episode_planning`、text_generation 全组 |
| `ctx.project_name`（入队、能力解析、TextGenerator 计量归属） | 所有 enqueue 工具 + text_generation 组 |

**已有的去 ctx 先例**：`text_generation.py::revalidate_reference_step1_draft` 刻意不依赖 ToolContext（`project_path`/`project` 由调用方传入），因为 web 审核 gate 的读时重算（`server/services/script_review.py`）没有 ctx、只有 ProjectManager——两处共用同一份代码。这正是「宿主无关 handler」的目标形态样板。

### 2.2 SDK 专属返回格式

- 所有 handler 返回 `{"content": [{"type": "text", "text": ...}], "is_error": bool}`；失败统一走 `_context.py::tool_error` / `_param_error`。**该形状就是 MCP `CallToolResult` 的 dict 形态，本身可移植**——真正 SDK 专属的只有两点：`claude_agent_sdk.tool` 装饰器（name/description/JSON schema 声明）与 `create_sdk_mcp_server` 注册方式。抽出时 handler 主体可原样保留，换注册壳即可。
- 返回文本全部是 **agent-facing 中文 + emoji 状态符（✅/❌/⚠️/⏸️/✨）**，按项目 i18n 规范豁免翻译（CLAUDE.md：MCP tool 返回不加 i18n key）。会话 locale（`options_assembler` 注入 system prompt 的语言规范）**没有下传到工具**；`text_generation.py` 用 `lib.i18n._`（全局 locale）渲染声音降级 warning。远程多语部署时这是一处隐性缺口。
- 返回里大量携带**项目相对路径**（`videos/scene_X.mp4`、`drafts/episode_N/...`、隔离草稿绝对路径），前提是 agent 与 server 共享同一文件系统视角（agent cwd = 项目目录）。

### 2.3 确认闸门 / 计费确认语义

没有任何一个闸门走 SDK hook 或 `can_use_tool`；全部是**工具协议层的两阶段参数**（第一次调用零副作用、返回待确认清单；用户同意后带确认位重调）：

1. **`confirm_duration`**（4 个 `generate_video_*`，参考路线）：unit 申请时长与剧本编排不一致时，首调**不入队任何任务**、返回 `DurationConfirmationPending` 清单（逐 unit：剧本时长 vs 申请时长 vs 调整方向）；用户同意后带 `confirm_duration=true` 重调完成入队。预检与 Web 端 `duration-precheck` 共用 `server/services/reference_video_tasks.py` 的同一取档规则。这是最接近「计费确认」的语义——确认的是将实际发给供应商（产生费用）的秒数。
2. **`confirm_consumed`**（`reset_episode_planning`）：重置波及已消费集（已有 step1/剧本/媒体产物）时不执行、返回受影响清单（`ResetConfirmationRequired`），须告知用户后带 `confirm_consumed=true` 重调。
3. **`confirm_script_review`**（独立工具）：step1→step2 审核 gate 的 agent 侧确认入口，与 Web 端确认**等价**（走同一 `server/services/script_review.py::ScriptReviewService.confirm`）。未确认时 `generate_episode_script` 与 step2 草稿晋升都被 `script_review.gate_blocks_step2` 拦下。工具描述要求「仅在用户对话中明确同意后调用」——闸门的最后一环是**对话约定**，机器上不设防。
4. **隔离草稿修复闭环**（`split_reference_video_units` → `open_reference_step1_for_edit` → `validate_and_promote_reference_draft`）：违约产物落 `QuarantinedDraft`（项目盘上文件）+ 逐条报告，agent **用 SDK 内建 Write/Edit 直接改草稿文件**后调晋升工具全量重判；晋升带乐观并发基线（`base_fingerprint` vs Web 端写入，冲突时返回合并报告）。
5. **在途任务防抢占**（`enqueue_videos.py::_assert_no_active_tasks`）：ad 点名重做前查 `get_active_tasks_for_resources`，命中 queued/running/cancelling 即整批拒绝——防止入队去重把「重做」静默折回旧任务。

### 2.4 对 session / hook 的隐性依赖

- **hook**：零依赖（见 §1）。唯一同场机制是 `_keep_stream_open_hook`（can_use_tool 时保持 stream）与 JSON 校验 hook——后者管的是 agent 用 Write/Edit 改项目 JSON 的路径，与隔离草稿工作流（§2.3-4）间接互补，但不构成工具依赖。
- **session**：MCP server 实例每会话新建；工具协程跑在 server 主事件循环里，单会话内被 `SessionActor` 串行化——**一次长工具调用占住整个会话回合**（最长 1 小时，见 §3）。
- **进程内单例**：`get_generation_queue()`（`lib/generation_queue.py` 模块级单例）、`lib.db.async_session_factory`、`ConfigResolver`。远程化时这些都要变成服务接口或共享 DB。
- **文件锁**：`locked_script` / `update_project` / `step1_write_lock` 均为**同主机文件锁**，与 Web 路由写入方共享；跨主机部署即失效。
- **user_id**：工具入队一律落 `DEFAULT_USER_ID`（`enqueue_task_only` 默认参数；`batch_enqueue_and_wait` 不传 user_id）。会话属主身份没有传导到任务归属——单用户部署无感，多租户远程 MCP 是必改点。
- **延迟 import**：`confirm_script_review` 内延迟 `from server.services.script_review import ...`，避免 sdk_tools 在 import 期耦合 server.services——现有代码已在意这条分层边。
- **agent 文件系统预设**：多个工具的**后续工序**假设 agent 能直接读写项目目录（编辑隔离草稿、读 step1/剧本、按返回的相对路径核对产物）。远程薄壳若不同机，这条工作流断裂——比工具本身更难迁移的是这套「工具 + 文件系统」混合协作面。

## 3. `enqueue_*` 工具长任务事实

### 3.1 队列客户端机制（`lib/generation_queue_client.py`）

- `enqueue_task_only`：入队前探测 worker 在线（不在线抛 `WorkerOfflineError`，**不入队**）；返回 `{"task_id": ...}`。
- `wait_for_task`：**每 1.0s 轮询**（`TASK_POLL_INTERVAL_SEC`）DB 任务态；默认超时 **3600s**（`DEFAULT_TASK_WAIT_TIMEOUT_SEC`，超时抛 `TaskWaitTimeoutError`）；等待期间 worker 掉线超过宽限期（`max(20s, 2×lease TTL 10s)` = 20s）抛 `WorkerOfflineError`。**超时/掉线只是停止等待，任务本身不取消、继续在队列里跑**——产物落盘但工具已报错返回，是「等待完成」语义的固有裂缝（checkpoint/幂等重入是其补偿，见下）。
- `enqueue_and_wait` = 入队 + 等待，failed/cancelled 转异常。
- `batch_enqueue_and_wait`：顺序入队（依赖解析需要顺序）+ `asyncio.gather` 并行等待，逐任务异常收进 failures 不中断整批；支持 `on_success`/`on_failure` 回调（视频工具用它逐成功写 checkpoint）。
- `TaskSpec.from_request` 是「可否入队」的**单一结构校验守卫点**，WebUI 路由与 SDK 工具共用——handler 抽出时这层天然可移植。

### 3.2 逐工具任务语义

| 工具 | 入队方式 | 等待语义 | 断点/幂等 |
|---|---|---|---|
| `generate_assets` | `batch_enqueue_and_wait` | 阻塞至整批终态 | 无 checkpoint；靠 pending 扫描天然幂等 |
| `edit_images` | `batch_enqueue_and_wait` | 同上 | 无；入队前 i2i 能力 fail-fast |
| `generate_storyboards` | `batch_enqueue_and_wait`（带依赖链 dependency_group/index） | 同上 | 失败写 `storyboards/generation_failures.json`；缺图扫描幂等 |
| `generate_grid` | 逐组 `enqueue_task_only` + `asyncio.gather(wait_for_task)` | 阻塞至全组终态 | 入队失败逐组回滚（`gm.delete`）；无 checkpoint |
| `generate_narration_audio` | `batch_enqueue_and_wait` | 阻塞 | 缺音频扫描幂等 |
| `generate_video_episode` | `batch_enqueue_and_wait`（`_submit_with_checkpoint`） | 阻塞 | `videos/.checkpoint_ep{N}.json` 逐成功落盘，`resume=true` 续传；成功后清 checkpoint |
| `generate_video_scene` | 单任务 `enqueue_and_wait` | 阻塞 | 无 |
| `generate_video_all` | `batch_enqueue_and_wait` | 阻塞 | 无 checkpoint；缺 video_clip 扫描幂等 |
| `generate_video_selected` | `batch_enqueue_and_wait`（`_submit_with_checkpoint`） | 阻塞 | 按规范 scene_ids 的 md5 哈希独立 checkpoint（`.checkpoint_selected_{hash}.json`），`resume=true` 续传；ad 点名路径**不落 checkpoint**（点名即强制覆盖，无续传语义） |

**结论：没有任何一个 enqueue 工具是「入队即返」**——全部等待完成（fire-and-forget 只存在于 `generate_grid` 的中间步，最终仍 gather 等待）。checkpoint + `resume` + 幂等扫描是对 1 小时阻塞窗口内中断（超时、会话断开、server 重启）的现行补偿机制。

另注：text_generation 组的四个 LLM 工具（`normalize_drama_script` / `split_narration_segments` / `split_reference_video_units` / `generate_episode_script`）与 `plan_episodes` 同样长耗时，但**不经队列**——进程内直接 await `TextGenerator`/`ScriptGenerator`/`EpisodePlanner`，无任务记录、无 `/api/v1/tasks` 可见性、无取消通道，仅有 `dry_run` 预览位。

### 3.3 任务结果查询路径

- HTTP：`GET /api/v1/tasks`（列表）、`GET /api/v1/tasks/{task_id}`、`GET /api/v1/projects/{name}/tasks`、cancel 系列（`server/routers/tasks.py`）。任务队列**无专属 SSE 通道**：终态经项目事件 SSE 触发前端刷新，中间态靠前端轮询 `/api/v1/tasks`（`frontend/src/hooks/useTaskRefresh.ts`）。
- **SDK 工具不消费这些 HTTP 端点**：handler 在进程内直接 `queue.get_task(task_id)` 轮询 DB。远程薄壳若要保留等待语义，要么复用 HTTP 轮询面，要么把等待挪回服务端、薄壳只拿 job handle。

## 4. 适配面清单（抽出宿主无关 handler + 远程 MCP 薄壳所需）

1. **项目作用域注入**：把 `ToolContext` 的闭包绑定改为「连接/会话级项目绑定」接口，保住「agent 永远不点名项目」的防注入不变量；`validate_script_filename` 等入参卫兵随 handler 走。
2. **注册壳与返回信封**：返回 dict 已是 MCP CallToolResult 形态，可原样保留；需要替换的只有 `claude_agent_sdk.tool` 装饰器与 `create_sdk_mcp_server`（换成远程 MCP server 的工具声明）。`ARCREEL_MCP_TOOL_IDS` → 前端 i18n 的 CI 契约要跟着注册表走。
3. **进程内单例 → 服务接口**：`get_generation_queue()`、`async_session_factory`、`ConfigResolver`、`ProjectManager`（含四把文件锁）目前都假设与 worker/Web 同进程同文件系统；跨主机需 DB 化或 RPC 化，文件锁需等价的跨主机互斥。
4. **长任务语义重定义**：现行「阻塞等待 + 1h 超时 + checkpoint/resume 补偿」不适合远程 MCP 长连接。候选：入队即返 job handle + 轮询工具（可复用 `/api/v1/tasks` 面）或 MCP progress/notification。现有 `resume`/幂等扫描参数已提供可重入语义，是迁移的现成资产；「超时后任务继续跑」的裂缝在远程化时应一并收口。
5. **确认闸门可直接移植**：三类确认（duration/consumed/script_review）都是无状态两阶段参数，不依赖宿主；但 gate 状态（审核指纹、隔离草稿）存于项目文件系统、与 Web 端共享——状态存储要和 #3 一起迁。
6. **「工具 + agent 文件系统」混合工作流**：隔离草稿要求 agent 用 Write/Edit 直改项目盘文件、返回文本大量引用项目相对路径。远程薄壳若 agent 不共享项目文件系统，需为草稿编辑补工具化通道（或收编进晋升工具的入参）。
7. **user_id 传导**：入队路径 hardcode `DEFAULT_USER_ID`，多租户远程部署须把会话属主传进 TaskSpec/enqueue。
8. **locale 传导**：工具返回豁免 i18n（agent-facing），但 `lib.i18n._` 的全局 locale 与会话 locale 脱钩；远程多语部署需显式传 locale 或维持豁免约定。
9. **text_generation 长调用无任务化**：LLM 直调工具（step1 组、plan_episodes）没有任务记录与取消通道，远程化时与 #4 同题——要么任务化，要么接受长阻塞。
10. **分层现状可借力**：`revalidate_reference_step1_draft` 的 ctx-free 设计、`TaskSpec.from_request` 的单一守卫点、`confirm_script_review` 的延迟 import，都是既有的「handler 与宿主解耦」方向样板，抽取时按此收敛而非另起口径。
