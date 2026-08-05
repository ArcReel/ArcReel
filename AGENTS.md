# ArcReel

ArcReel 是一个 AI 视频生成平台，将小说转化为短视频。三层架构：

```
frontend/ (React SPA)  →  server/ (FastAPI)  →  lib/ (核心库)
  React 19 + Tailwind       路由分发 + SSE
  wouter 路由               agent_runtime/
  zustand 状态管理          (Claude Agent SDK)
```

## 开发命令

```bash
# 后端
# 启动开发服务器（必须用 --reload-dir 限定监视目录，否则 watchfiles 会扫描
# node_modules / .venv / .git / .worktrees 等十几万个文件，单核 CPU 50%+）
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241

uv run python -m pytest                              # 测试（-v 单文件 / -k 关键字 / --cov 覆盖率）
uv run ruff check . && uv run ruff format .          # lint + format
uv run basedpyright                                  # 类型检查（CI 强制 0 error）
uv run lint-imports                                  # import 分层契约（CI backend-tests 必过）
uv sync                                              # 安装依赖
uv run alembic upgrade head                          # 数据库迁移
uv run alembic revision --autogenerate -m "desc"     # 生成迁移

# 前端，先 cd frontend
pnpm lint        # ESLint，CI frontend-tests 第一段，含 jsx-a11y 规则
pnpm check       # typecheck + vitest
pnpm build       # 生产构建，含 typecheck
# 前端 CI 等价：pnpm lint && pnpm check，push 前两者均须通过
```

## 架构要点

### 后端

- `characters.py` / `scenes.py` / `props.py` 路由由 `_asset_router_factory.build_asset_router()` 统一生成，按 `lib/asset_types.ASSET_SPECS` 驱动；新增资产类型时只需在 spec 注册
- `resume_executor.py` 是 worker `_process_resume_task` 入口，不经由常规视频流水线，仅复用 finalize helpers 写回资产
- 图片指令式编辑（`image_edit_tasks.py`）的设计见 `docs/adr/0050`
- 数据库：开发 SQLite（`projects/.arcreel.db`），生产 PostgreSQL（`asyncpg`）

### Agent Runtime（Claude Agent SDK 集成）

`server/agent_runtime/` 封装 Claude Agent SDK：
- `SessionActor` — 每会话一个专属 asyncio task，串行化所有 ClaudeSDKClient 调用（spec: `docs/superpowers/specs/2026-04-13-session-actor-design.md`）
- `SessionStore` 的 transcript DB 镜像受 `ARCREEL_SDK_SESSION_STORE` 环境变量控制：`db`/`off`，off 时回退到 SDK 自带的 jsonl 路径
- `sdk_tools/` 内的 SDK 进程内 MCP 工具由 agent profile manifest 注入，供 Skill 调用

## 关键设计模式

### 数据分层

| 数据类型 | 存储位置 | 策略 |
|---------|---------|------|
| 角色/场景/道具定义 | `project.json`（项目级）+ `assets` 表（全局库） | 单一真相源，剧本中仅引用名称；三类资产共用 `lib/asset_types.ASSET_SPECS` 抽象 |
| 剧集元数据（episode/title/script_file） | `project.json` | 剧本保存时写时同步 |
| 统计字段（scenes_count / status / progress） | 不存储 | `StatusCalculator` 读时计算注入 |

### 实时通信

- 助手：`/api/v1/assistant/sessions/{id}/stream` — SSE 流式回复
- 项目事件：`/api/v1/projects/{name}/events/stream` — SSE 推送项目变更
- 任务队列：无专属 SSE 通道——终态变更经项目事件 SSE 触发刷新，中间态与兜底由前端轮询 `/api/v1/tasks`（机制详见 `frontend/src/hooks/useTaskRefresh.ts` 注释）

### 任务队列

所有生成任务（分镜/视频/角色/场景/道具/参考视频）统一通过 GenerationQueue 入队，由 GenerationWorker 异步处理（image / video 两条独立并发通道）。
`generation_queue_client.py` 的 `enqueue_and_wait()` 封装入队 + 等待完成。

### 数据校验

`lib/data_validator.py` 验证 `project.json` 和剧集 JSON 的结构与引用完整性。

### 供应商能力数据

生成模型供应商的能力数据按字段划分真相源：视频能力位与各类上限归对应 backend（`VideoCapabilities`，与请求构造同源，见 `docs/adr/0054`）；图片能力位归 `PROVIDER_REGISTRY` 的 `ModelInfo.capabilities`（自定义供应商归 endpoint 声明）；其余能力数字与默认 model 归 `PROVIDER_REGISTRY`，`supported_durations` 未登记即 fail loud、无隐性 fallback（见 `docs/adr/0018`；仅时长联动约束在未登记型号上有 backend 兜底常量），自定义模型改读 DB 声明。自定义供应商（`custom-` 前缀）与智能体供应商预设的其余字段（不含图片能力位）不适用上述按 `PROVIDER_REGISTRY` 划分的真相源。prompt 模板与智能体运行配置（`agent_runtime_profile/`）不硬编码具体数值，占位符由编排层注入（供应商 API 文档镜像保留原始数值）；配置界面此类字段不预填。陷阱：个别 backend（如 vidu 的执行期分辨率白名单）独立于 registry，改 registry 分辨率声明时须同步核对对应 backend，否则用户可选但 backend 不认的档位会被静默替换为兜底档位。

### 内容模式 (content_mode) 与生成模式 (generation_mode)

两个独立维度，分别承载"内容类型"与"视频来源"：

- **content_mode** — `drama`（剧集，内容驱动）/`narration`（说书，旁白驱动） / `ad`（广告/短片）。决定剧本结构与 agent profile 加载哪个 `CLAUDE.*.md` 变体
- **generation_mode** — 项目级**生成路线**，二值必填：`storyboard`（分镜路线，分镜图驱动，走 i2v）/ `reference_video`（参考路线，无需分镜图步骤，直接使用资产图作为参考图生成视频，见 `lib/reference_video/`）。创建时二选一、创建后不可更改；宫格不是路线，由独立的项目级布尔 `grid_storyboard` 表达（见 `docs/adr/0055`）
- 两字段均对 LLM 隐藏：`content_mode` 是剧本模型上的 `SkipJsonSchema` 字段、由编排层写入；`generation_mode` 只存 project.json，剧本不携带该字段

## Agent 沙箱

Linux 默认通过 bwrap、macOS 通过 sandbox-exec 在 Agent 工具调用外围加一层隔离（文件系统/网络/子进程白名单），
由 `server/app.py::check_sandbox_available` 探测并启用。写新 Agent 工具时假设沙箱**默认开启**：
路径越界、白名单外网络请求会被拒绝，需要时显式声明权限。

Windows 原生无 bwrap，会自动降级：

- `check_sandbox_available` 返回 False，Agent Bash 工具回退到 `_WINDOWS_BASH_PREFIX_WHITELIST` 代码白名单
  （比沙箱粗粒度，可放行的命令前缀有限），WSL2/Docker 部署仍走完整沙箱
- 新增 Agent 工具时如果依赖沙箱内才有的能力（如 bind mount 隔离 cwd），需要编写 Windows 下的降级路径，
  或在 `check_sandbox_available()` 失败时显式拒绝运行而非静默放行

## 智能体运行环境

ArcReel 内嵌基于 Claude Agent SDK 的智能体（Harness 即上文的 Agent Runtime），其专属配置的源目录是 `agent_runtime_profile/`，与开发态 `.claude/` 物理分离：

- `agent_runtime_profile/.claude/skills/`、`agent_runtime_profile/.claude/agents/` — Skill 与 Subagent 定义
- `agent_runtime_profile/CLAUDE.*.md` — 按 `content_mode` 拆分的系统 prompt 变体，运行时按项目内容模式动态注入
- `lib/profile_manifest.py` 把上述配置同步到各用户项目的 `.claude/` 与 CLAUDE.md，智能体从项目侧加载；同步以 manifest + sha256 识别用户改过的项目侧文件并保留，不覆盖

Skill 的创建、评估和维护流程参考 `/skill-creator` skill。

- **SKILL.md 与脚本同步**：修改 skill 脚本时需同步更新 SKILL.md，反之亦然

## 国际化 (i18n) 规范

- 禁止硬编码中文字符串，新增面向用户的文本须同时添加 `zh`/`en`/`vi` 翻译 key
- **仅面向用户的文本需 i18n**：router 响应 / email / 前端文本走 Translator；仅面向 agent 的字符串（MCP tool 返回、agent prompt、service 层异常、logger）豁免，不要为其加翻译 key
- 后端：`_t: Translator` 依赖注入；前端：`useTranslation("namespace")`
- CI 有 `tests/test_i18n_consistency.py` 校验 zh/en/vi 三语 key 不漂移

## 环境配置

复制 `.env.example` 到 `.env`，设置认证参数（`AUTH_USERNAME`/`AUTH_PASSWORD`/`AUTH_TOKEN_SECRET`）。
API Key、后端选择、模型配置等通过 WebUI 配置页（`/settings`）管理。
外部工具依赖：`ffmpeg`（视频拼接与后期处理）。

## Windows 兼容性

主开发平台是 macOS / Linux，但 server 必须能在 Windows 上完成项目创建与基础流程。涉及文件系统、子进程、tmp 路径、权限的新代码遵循：

- **POSIX-only `os` 常量** — `O_NOFOLLOW` / `O_DIRECT` 等用 `getattr(os, "O_NOFOLLOW", 0)`，Python 层 `is_symlink()` 兜底（例：`lib/profile_manifest.py::_project_lock`）
- **`os.chmod(0o600)`** — 以 `if os.name == "posix":` 包裹；Windows 凭证保护交给 ACL（用户级 `%LOCALAPPDATA%`）
- **文件 I/O 显式 `encoding="utf-8"`** — 否则 Windows 默认 cp936/cp1252 会破坏 UTF-8 文本
- **tmp 路径用 `tempfile.gettempdir()`** — 不硬编码 `/tmp`；匹配 Claude SDK tmp 输出时 tempdir 与 POSIX 别名须同时列出
- **subprocess 用 `create_subprocess_exec`（list 形式）** — 避免 `shell=True`；ffmpeg/ffprobe 先 `shutil.which()` 探测，缺失时降级处理而非直接失败
- **长路径** — Windows 10 1607+ 需 `LongPathsEnabled=1` 解除 MAX_PATH (260) 限制

## 代码质量

- **ruff**：line-length 120，提交前对修改的 Python 文件执行 `uv run ruff check <files> && uv run ruff format <files>`
- **basedpyright**：standard 模式 + `reportMissingTypeStubs = false`，CI 强制 0 error，pre-push hook 跑全量扫描；本地可随时执行 `uv run basedpyright` 校验。tests/ 内 `reportOptional*` 和 `unknown*` 系列降级为 warning，避免大量使用 mock 的测试产生噪声；第三方 untyped 库（ffmpeg-python、pyJianYingDraft、volcenginesdkarkruntime、xai_sdk.chat、docx2txt/mammoth/ebooklib）通过行级 `# pyright: ignore[...]` 处理
- **import-linter**：`uv run lint-imports` 校验 `lib.config < lib.*_backends < lib.custom_provider` 分层契约，CI backend-tests 必过步骤；新增 ignore 条目前先确认该依赖边无法就地清零（约定见 pyproject.toml）
- **pytest**：`asyncio_mode = "auto"`，CI 覆盖率 ≥80%，共用 fixtures 在 `tests/conftest.py`。测试替身优先复用/扩展 `tests/fakes.py`，新建 fake 入该模块；触碰含文件内私有 fake 的测试文件时顺带迁移
- **依赖管理**：前后端新增/升级依赖一律用 `uv add` / `pnpm add`（不手写版本号到 pyproject.toml / package.json）；新增依赖后同步 `.github/dependabot.yml` 的 patterns 归入对应分组
- **注释**：代码与测试注释只描述当下行为与约束，不写 issue/PR/Spec 编号，也不用时间性措辞（「最近」「本次」「实测」）——这些信息写在 commit message / PR 描述；修改文件时顺带清除已有的此类引用。`docs/` 下专门文档之间互引 spec 不受此限
- **提交与 PR**：标题遵循 Conventional Commits（`type(scope): 摘要`，type 取值与 changelog 分类见 `CONTRIBUTING.md` / `.release-please-config.json`）。squash 合并下标题即 changelog 条目——写用户可感知的收益、范围词用产品术语，不写实现术语（status_code、内部类名等）且如实限定范围。前后端同仓一体发布，后端 API 不做版本化对外承诺（外部集成经 `/skill.md` 运行时拉取契约，删改 `public/skill.md.template` 引用的端点时同步更新该模板）：接口删改按 `fix`/`refactor` 正常分类，不加 `!` 后缀、不写 `BREAKING CHANGE:` footer

## Agent skills

### Issue tracker

议题（issue/Spec）追踪在 `ArcReel/ArcReel` 的 GitHub Issues，统一用 `gh` CLI 操作。Spec 用 `Spec` 标签 + `Spec:` 标题前缀；细分 issue 标题尾缀 `[Spec #N]` 并挂原生 sub-issue。详见 `docs/agents/issue-tracker.md`。

### Triage labels

triage 状态机使用五个默认标签：`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`，另有 `parked` 标记刻意搁置的 issue。详见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文布局：根目录 `CONTEXT.md` + `docs/adr/`。详见 `docs/agents/domain.md`。
