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

### 内容模式 (content_mode) 与生成模式 (generation_mode)

两个独立维度，分别承载"内容类型"与"视频来源"：

- **content_mode** — `drama`（剧集，内容驱动）/`narration`（说书，旁白驱动） / `ad`（广告/短片）。决定剧本结构与 agent profile 加载哪个 `CLAUDE.*.md` 变体
- **generation_mode** — `reference_video` 等。决定视频生成路径：图生视频（默认，分镜图驱动）/ 宫格生视频（grid_4/6/9）/ 参考生视频（无需分镜图步骤，直接使用资产图作为参考图生成视频，见 `lib/reference_video/`）
- 两字段对 LLM 隐藏（`SkipJsonSchema`），由编排层自动注入

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

智能体专用配置的源目录是 `agent_runtime_profile/`，与开发态 `.claude/` 物理分离，其下：

- `.claude/skills/` `.claude/agents/` — Skill 与 Subagent 定义
- `CLAUDE.*.md` — 按 `content_mode` 拆分的系统 prompt 变体，运行时按项目内容模式动态注入
- profile 同步由 `lib/profile_manifest.py` 通过 manifest + sha256 驱动，仅把声明过且校验通过的文件单向复制到各项目的 `.claude/`，运行时从项目侧加载；直接改项目侧文件不会生效且会被下次同步覆盖

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
- **pytest**：`asyncio_mode = "auto"`，CI 覆盖率 ≥80%，共用 fixtures 在 `tests/conftest.py`
- **依赖管理**：前后端新增/升级依赖一律用 `uv add` / `pnpm add`（不手写版本号到 pyproject.toml / package.json）；新增依赖后同步 `.github/dependabot.yml` 的 patterns 归入对应分组
- **提交与 PR**：标题遵循 Conventional Commits（`type(scope): 摘要`，type 取值与 changelog 分类见 `CONTRIBUTING.md` / `.release-please-config.json`）。squash 合并下标题即 changelog 条目——写用户可感知的收益、范围词用产品术语，不写实现术语（status_code、内部类名等）且如实限定范围

## Agent skills

### Issue tracker

议题（issue/Spec）追踪在 `ArcReel/ArcReel` 的 GitHub Issues，统一用 `gh` CLI 操作。Spec 用 `Spec` 标签 + `Spec:` 标题前缀；细分 issue 标题尾缀 `[Spec #N]` 并挂原生 sub-issue。详见 `docs/agents/issue-tracker.md`。

### Triage labels

triage 状态机使用五个默认标签：`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`，另有 `parked` 标记刻意搁置的 issue。详见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文布局：根目录 `CONTEXT.md` + `docs/adr/`。详见 `docs/agents/domain.md`。
