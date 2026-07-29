# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

ArcReel 是一个 AI 视频生成平台，将小说转化为短视频。三层架构：

```
frontend/ (React SPA)  →  server/ (FastAPI)  →  lib/ (核心库)
  React 19 + Tailwind       路由分发 + SSE
  wouter 路由               agent_runtime/
  zustand 状态管理          (Claude Agent SDK)
```

---

## ⚡ 开发命令

### 后端

```bash
# 启动开发服务器（必须用 --reload-dir 限定监视目录，否则 watchfiles 会扫描
# node_modules / .venv / .git / .worktrees 等十几万个文件，单核 CPU 50%+）
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241

uv run python -m pytest                              # 全部测试
uv run python -m pytest -v -k "keyword"              # 按关键字筛选
uv run python -m pytest --cov=lib --cov=server       # 带覆盖率
uv run ruff check . && uv run ruff format .          # lint + format（line-length 120）
uv run basedpyright                                  # 类型检查（CI 强制 0 error）
uv sync                                              # 安装/同步依赖
uv run alembic upgrade head                          # 数据库迁移
uv run alembic revision --autogenerate -m "desc"     # 生成迁移
```

### 前端（先 `cd frontend`）

```bash
pnpm dev              # Vite 开发服务器（端口 5173，/api 代理到 1241）
pnpm build            # 生产构建（含 typecheck）
pnpm lint             # ESLint（CI 强制通过）
pnpm lint:fix         # 自动修复
pnpm typecheck        # tsc --noEmit
pnpm check            # typecheck + lint + vitest run
pnpm test             # vitest run
pnpm test:coverage    # vitest 带覆盖率
pnpm test:watch       # vitest 监听模式
```

### 依赖管理

- 新增依赖：`uv add <pkg>`（Python）/ `pnpm add <pkg>`（前端）—— 不要手写版本号
- 加完同步 `.github/dependabot.yml` 的 patterns 归入对应分组

---

## 🏗 架构要点

### 后端 API 路由（`server/routers/`）

所有 API 在 `/api/v1` 下：
- `projects.py` — 项目 CRUD、概述生成
- `generate.py` — 分镜/视频/角色/场景/道具生成入队
- `assistant.py` — Claude Agent SDK 会话管理（SSE 流式）
- `agent_chat.py` — 智能体对话交互
- `tasks.py` — 任务队列状态（SSE 流式）
- `project_events.py` — 项目事件 SSE 推送
- `files.py` — 文件上传与静态资源
- `versions.py` — 资源版本历史与回滚
- `characters.py` / `scenes.py` / `props.py` — 项目级资产 CRUD（**由 `_asset_router_factory.build_asset_router()` 统一生成**，按 `lib/asset_types.ASSET_SPECS` 驱动）
- `assets.py` — 全局资产库（跨项目复用）
- `reference_videos.py` — 参考视频→视频生成
- `auth.py` / `api_keys.py` — 认证与 API 密钥管理
- `system_config.py` / `providers.py` / `custom_providers.py` — 系统 & 供应商配置
- `agent_config.py` — Agent Anthropic 凭证管理
- `usage.py` / `cost_estimation.py` / `grids.py` — 用量 / 费用 / 宫格图

### 后端服务层（`server/services/`）

- `generation_tasks.py` — 生成任务编排（分镜/视频/角色/场景/道具）
- `image_edit_tasks.py` — 图片指令式编辑（i2i 微调）
- `reference_video_tasks.py` — 参考视频任务编排
- `project_archive.py` / `project_cover.py` / `project_events.py` — 项目导出/封面/事件
- `jianying_draft_service.py` — 剪映草稿导出
- `cost_estimation.py` / `resume_executor.py` / `diagnostics.py`

### 核心库（`lib/`）

- **Media backends**（`image_backends/` / `video_backends/` / `text_backends/` / `audio_backends/`）— 多供应商，Registry + Factory 模式
- **Custom provider**（`custom_provider/`）— OpenAI/Google 兼容 API
- **Supplier SDK**（`*_shared.py`）— Gemini / Ark / Grok / OpenAI / DashScope / Vidu / Kling / MiniMax
- **MediaGenerator** — 组合后端 + VersionManager + UsageTracker
- **GenerationQueue** — 异步任务队列，SQLAlchemy ORM 后端，lease-based 并发控制
- **GenerationWorker** — 后台 Worker，分 image/video 两条独立并发通道
- **ProjectManager**（~106KB）— 项目文件系统操作
- **StatusCalculator** — 读时计算状态，不存储冗余状态
- **script_models.py** / **script_generator.py** / **script_editor.py** / **data_validator.py** — 剧本模型与验证
- **asset_types.py** — character/scene/prop 三类资产的统一 spec（`ASSET_SPECS`），驱动路由工厂、bucket key、字段白名单
- **source_loader/** — 小说源文件导入（txt/docx/epub/pdf）
- **reference_video/** / **grid/** — 参考视频 & 宫格图系统
- **prompt_builders.py** / **prompt_builders_script.py** / **prompt_builders_ad.py** — prompt 构建
- **retry.py** — 通用指数退避重试装饰器

### 数据库（`lib/db/`）

- 异步引擎（SQLAlchemy 2.0 async），开发用 SQLite（`projects/.arcreel.db`），生产 PostgreSQL
- ORM 模型：Task / ApiCall / ApiKey / AgentSession / Config / Credential / User / CustomProvider / Asset
- Repository 模式：Task / Usage / Session / ApiKey / Credential / CustomProvider / Asset
- 迁移：Alembic（`alembic/`）

### Agent Runtime（`server/agent_runtime/`）

封装 Claude Agent SDK：
- `AssistantService` — 编排 SDK 会话
- `SessionManager` — 会话生命周期 + SSE 订阅者模式
- `SessionActor` — 每会话一个专属 asyncio task，串行化所有 ClaudeSDKClient 调用
- `SessionStore` — 会话元数据 + transcript DB 镜像
- `sdk_tools/` — 进程内 MCP 工具（enqueue_assets / grid / storyboards / videos / text_generation）

Agent 运行时 profile 在 `agent_runtime_profile/`，与开发态 `.claude/` 物理分离。Skill 和 Subagent 定义在 `.claude/skills/` / `.claude/agents/`。按 `content_mode`（narration/drama）加载不同的 `CLAUDE.*.md` 变体。

### 前端

- React 19 + TypeScript strict + Tailwind CSS 4 + Vite 8
- 路由：`wouter`（非 React Router），路径别名 `@/` → `frontend/src/`
- 状态管理：`zustand`，stores 在 `frontend/src/stores/`
- **入队走动作层**：生成类操作经 `frontend/src/actions/` 封装，不直调入队 API；新增入队方法时同步登记到 `eslint.config.js` 的 `no-restricted-syntax`
- **占用感知型控件接线**：编辑/重生成/上传等控件随资源占用态禁用，打开时 + 提交前双重校验，兄弟控件同步
- i18n：`i18next` + `react-i18next`，`frontend/src/i18n/{zh,en,vi}/`

---

## 🎯 关键设计模式

### 数据分层

| 数据类型 | 存储位置 | 策略 |
|---------|---------|------|
| 角色/场景/道具定义 | `project.json` + `assets` 表（全局库） | 单一真相源，剧本中仅引用名称 |
| 剧集元数据 | `project.json` | 保存时写时同步 |
| 统计字段 | 不存储 | `StatusCalculator` 读时计算注入 |

### 实时通信

- 助手：`/api/v1/assistant/sessions/{id}/stream` — SSE 流式回复
- 项目事件：`/api/v1/projects/{name}/events/stream` — SSE 推送
- 任务队列：前端轮询 `/api/v1/tasks`

### 任务队列

所有生成任务通过 GenerationQueue 入队，由 GenerationWorker 异步处理（image / video 两条独立并发通道）。
`generation_queue_client.py` 的 `enqueue_and_wait()` 封装入队 + 等待完成。

### 内容模式与生成模式（独立维度）

- **content_mode**：`narration`（说书，按朗读节奏拆片段）/ `drama`（剧集动画，按场景对话组织）
- **generation_mode**：`reference_video` 等跳过分镜直出视频
- 两字段对 LLM 隐藏（`SkipJsonSchema`），由编排层注入

### 供应商配置系统

`lib/config/` — ConfigService → Repository（持久化 + 密钥脱敏）→ Resolver → ProviderRegistry

---

## 🌐 国际化规范

- **禁止硬编码中文字符串**，新增面向用户的文本须同时添加 `zh`/`en`/`vi` 翻译 key
- 仅面向 agent 的字符串（MCP tool 返回、agent prompt、异常、logger）豁免
- 后端：`_t: Translator` 依赖注入；前端：`useTranslation("namespace")`
- CI 有 `tests/test_i18n_consistency.py` 校验三语 key 不漂移

---

## 🪟 Windows 兼容性

主开发平台是 macOS / Linux，但 server 须能在 Windows 上完成项目创建与基础流程：

- **POSIX-only `os` 常量** — 用 `getattr(os, "O_NOFOLLOW", 0)` 兜底
- **`os.chmod(0o600)`** — 包 `if os.name == "posix":` guard
- **文件 I/O** — 显式 `encoding="utf-8"`（否则 Windows 默认 cp936）
- **tmp 路径** — 用 `tempfile.gettempdir()`，不硬编码 `/tmp`
- **subprocess** — 用 `create_subprocess_exec`（list 形式），避免 `shell=True`
- **ffmpeg/ffprobe** — 先 `shutil.which()` 探测，缺失时降级
- **Sandbox** — Windows 自动降级到 `_WINDOWS_BASH_PREFIX_WHITELIST` 白名单

---

## 📝 代码质量

- **ruff**：line-length 120，提交前对修改的 Python 文件执行 `uv run ruff check <files> && uv run ruff format <files>`
- **basedpyright**：standard 模式，CI 强制 0 error（tests/ 内 `reportOptional*` 等降级为 warning）
- **pytest**：`asyncio_mode = "auto"`，CI 覆盖率 ≥80%
- **新测试必须打 marker**：`unit`（快速隔离）/ `integration`（跨模块协作）/ `e2e`（依赖外部资源，CI 默认跳过）—— 现存测试不强制回溯
- **ESLint**：零 warning 政策，所有规则为 error；disable 须带 `// eslint-disable-next-line <rule> -- <中文理由>`
- **类型检查**：前端 `pnpm typecheck`（tsc --noEmit），后端 `basedpyright`

---

## 🔧 贡献流程

- **分支策略**：trunk-based，所有工作从最新 `main` 切短分支（`<type>/<slug>`），≤3 天合回
- **提交**：Conventional Commits（`type(scope): 摘要`），squash merge 到 main
- **禁止直推** `git push origin main`
- **发布**：由 release-please 自动管理版本号与 changelog，开发者无需手动 bump
- **发版类型**：`feat` → minor，`fix` → patch，`feat!`/`BREAKING CHANGE` → major

---

## 📚 深入阅读

| 文档 | 内容 |
|------|------|
| `AGENTS.md` | 完整架构、设计模式、Agent 沙箱、i18n 规范——本文件的详细扩展 |
| `CONTRIBUTING.md` | 贡献流程、分支策略、提交规范、发版流程、ESLint 规范 |
| `.claude/rules/frontend-async-race.md` | 前端异步竞态防护：AbortSignal 跨函数取消 + store action 在途合并 |
| `.claude/rules/onboarding-anchors.md` | 引导锚点防腐：修改/删除 data-onboarding 元素时的连带核对清单 |
| `CONTEXT.md` | 领域上下文文档（57KB） |
| `docs/adr/` | 架构决策记录 |
| `README.md` | 项目概述、功能表、供应商支持矩阵 |
