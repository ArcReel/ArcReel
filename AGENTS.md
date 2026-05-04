# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 语言规范
- **回答用户必须使用中文**：所有回复、任务清单及计划文件，均须使用中文

## 项目概述

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
uv sync                                              # 安装依赖
uv run alembic upgrade head                          # 数据库迁移
uv run alembic revision --autogenerate -m "desc"     # 生成迁移

# 前端，先 cd frontend
pnpm lint        # ESLint，CI frontend-tests 第一段，含 jsx-a11y 规则
pnpm check       # typecheck + vitest
pnpm build       # 生产构建，含 typecheck
# CI 等价：pnpm lint && pnpm check —— push 前两条都要绿
```

## 架构要点

### 后端 API 路由

所有 API 在 `/api/v1` 下，路由定义在 `server/routers/`：
- `projects.py` — 项目 CRUD、概述生成
- `generate.py` — 分镜/视频/角色/场景/道具生成（入队到任务队列）
- `assistant.py` — Claude Agent SDK 会话管理（SSE 流式）
- `agent_chat.py` — 智能体对话交互
- `tasks.py` — 任务队列状态（SSE 流式）
- `project_events.py` — 项目事件 SSE 推送
- `files.py` — 文件上传与静态资源
- `versions.py` — 资源版本历史与回滚
- `characters.py` / `scenes.py` / `props.py` — 项目级资产 CRUD（**由 `_asset_router_factory.build_asset_router()` 统一生成**，按 `lib/asset_types.ASSET_SPECS` 驱动；新增资产类型时只需在 spec 注册）
- `assets.py` — 全局资产库（跨项目复用的 character/scene/prop，DB 持久化于 `assets` 表）
- `reference_videos.py` — 参考视频→视频生成（按镜头解析 + 入队）
- `usage.py` — API 用量统计
- `cost_estimation.py` — 费用预估（项目/单集/单镜头）
- `grids.py` — 宫格图生成、列表、详情、重新生成
- `auth.py` / `api_keys.py` — 认证与 API 密钥管理
- `system_config.py` — 系统配置
- `providers.py` — 预置供应商配置管理（列表、读写、连接测试）
- `custom_providers.py` — 自定义供应商 CRUD、模型管理与发现、连接测试

### server/services/ — 业务服务层

- `generation_tasks.py` — 分镜/视频/角色/场景/道具生成任务编排
- `reference_video_tasks.py` — 参考视频→视频生成任务编排
- `project_archive.py` — 项目导出（ZIP 打包）
- `project_cover.py` — 项目封面生成
- `project_events.py` — 项目变更事件发布
- `jianying_draft_service.py` — 剪映草稿导出
- `cost_estimation.py` — 费用预估计算与实际费用汇总
- `resolution_resolver.py` — 视频分辨率解析（按 provider 能力适配）

### lib/ 核心模块

- **{gemini,ark,grok,openai}_shared** + **httpx_shared** — 各供应商 SDK 工厂与共享工具
- **image_backends/** / **video_backends/** / **text_backends/** — 多供应商媒体生成后端，Registry + Factory 模式（gemini/ark/grok/openai；video 还有 newapi）
- **custom_provider/** — 自定义供应商支持：后端包装、模型发现、工厂创建（OpenAI/Google 兼容）
- **MediaGenerator** (`media_generator.py`) — 组合后端 + VersionManager + UsageTracker
- **GenerationQueue** (`generation_queue.py`) — 异步任务队列，SQLAlchemy ORM 后端，lease-based 并发控制
- **GenerationWorker** (`generation_worker.py`) — 后台 Worker，分 image/video 两条并发通道
- **ProjectManager** (`project_manager.py`) — 项目文件系统操作和数据管理
- **StatusCalculator** (`status_calculator.py`) — 读时计算状态字段，不存储冗余状态
- **UsageTracker** (`usage_tracker.py`) / **CostCalculator** (`cost_calculator.py`) — 用量追踪与费用计算
- **TextGenerator** (`text_generator.py`) / **ScriptGenerator** (`script_generator.py`) — 文本与剧本生成
- **asset_types.py** — character/scene/prop 三类资产的统一 spec（`ASSET_SPECS`），驱动路由工厂、bucket key、sheet 字段、PATCH 白名单
- **source_loader/** — 小说源文件导入（txt/docx/epub/pdf），统一 `loader` 接口
- **reference_video/** — 参考视频→视频：`shot_parser` 按镜头解析 prompt + `limits` 容量约束
- **grid/** — 宫格图系统：布局计算（grid_4/6/9）、prompt 构建、切割
- **agent_session_store/** — Claude Agent SDK transcript 入库镜像（store + import_local）
- **retry** (`retry.py`) — 通用指数退避重试装饰器，各供应商后端复用

### lib/config/ — 供应商配置系统

ConfigService（`service.py`）→ Repository（持久化 + 密钥脱敏）→ Resolver（解析）。`registry.py` 维护预置供应商注册表（PROVIDER_REGISTRY）。

### lib/db/ — SQLAlchemy Async ORM 层

- `engine.py` — 异步引擎 + session factory（`DATABASE_URL` 默认 `sqlite+aiosqlite`）
- `models/` — ORM 模型：Task / ApiCall / ApiKey / AgentSession（`session.py`）/ Config / Credential / User / CustomProvider（含模型子表）/ **Asset**（全局资产库）
- `repositories/` — 异步 Repository：Task / Usage / Session / ApiKey / Credential（多 API Key + 活跃切换）/ CustomProvider / **Asset**

数据库：开发 SQLite（`projects/.arcreel.db`），生产 PostgreSQL（`asyncpg`）

### Agent Runtime（Claude Agent SDK 集成）

`server/agent_runtime/` 封装 Claude Agent SDK：
- `AssistantService` (`service.py`) — 编排 Claude SDK 会话
- `SessionManager` — 会话生命周期 + SSE 订阅者模式
- `SessionActor` (`session_actor.py`) — 每会话一个专属 asyncio task，串行化所有 ClaudeSDKClient 调用（spec: `docs/superpowers/specs/2026-04-13-session-actor-design.md`）
- `SessionStore` (`session_store.py`) — 会话元数据 + transcript DB 镜像（受 `ARCREEL_SDK_SESSION_STORE` 环境变量控制：`db`/`off`，off 时回退到 SDK 自带的 jsonl 路径）
- `StreamProjector` — 从流式事件构建实时助手回复
- `turn_grouper` / `turn_schema` / `transcript_reader` — transcript 分组与读取（用于历史回放）

### lib/i18n/ — 国际化

后端翻译层，支持 `zh`/`en` 两种语言。`{zh,en}/` 各文件按命名空间拆分：`errors`（错误与校验）、`providers`（供应商名称/描述）、`assets`（资产相关消息）、`emails`（邮件模板）、`system`（系统消息）、`templates`（模板消息）。
- `Translator` 类型 = `Annotated[Callable[..., str], Depends(get_translator)]`，从 `Accept-Language` 解析语言
- 路由中通过 `_t: Translator` 依赖注入，调用 `_t("key", param=value)` 获取翻译文本

### 前端

- React 19 + TypeScript + Tailwind CSS 4
- 路由：`wouter`（非 React Router）
- 状态管理：`zustand`（stores 在 `frontend/src/stores/`）
- 路径别名：`@/` → `frontend/src/`
- Vite 代理：`/api` → `http://127.0.0.1:1241`
- i18n：`i18next` + `react-i18next`，翻译文件在 `frontend/src/i18n/{zh,en}/`，命名空间 `common`/`dashboard`/`auth`/`errors`/`assets`/`templates`

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
- 任务队列：前端轮询 `/api/v1/tasks` 获取状态

### 任务队列

所有生成任务（分镜/视频/角色/场景/道具/参考视频）统一通过 GenerationQueue 入队，由 GenerationWorker 异步处理（image / video 两条独立并发通道）。
`generation_queue_client.py` 的 `enqueue_and_wait()` 封装入队 + 等待完成。

### Pydantic 数据模型

`lib/script_models.py` 定义 `NarrationSegment` 和 `DramaScene`，用于剧本验证。
`lib/data_validator.py` 验证 `project.json` 和剧集 JSON 的结构与引用完整性。

## 智能体运行环境

智能体专用配置（skills、agents、系统 prompt）位于 `agent_runtime_profile/` 目录，
与开发态 `.claude/` 物理分离。Skill 的创建、评估和维护流程参考 `/skill-creator` skill。

- **SKILL.md 与脚本同步**：修改 skill 脚本时需同步更新 SKILL.md，反之亦然，二者必须保持一致

## 国际化 (i18n) 规范

- 禁止硬编码中文字符串，新增面向用户的文本须同时添加 `zh`/`en` 翻译 key
- 后端：`_t: Translator` 依赖注入；前端：`useTranslation("namespace")`
- CI 有 `test_i18n_consistency.py` 校验 key 漂移

## 环境配置

复制 `.env.example` 到 `.env`，设置认证参数（`AUTH_USERNAME`/`AUTH_PASSWORD`/`AUTH_TOKEN_SECRET`）。
API Key、后端选择、模型配置等通过 WebUI 配置页（`/settings`）管理。
外部工具依赖：`ffmpeg`（视频拼接与后期处理）。

### 代码质量

- **ruff**：line-length 120，提交前对修改的 Python 文件执行 `uv run ruff check <files> && uv run ruff format <files>`
- **pytest**：`asyncio_mode = "auto"`，CI 覆盖率 ≥80%，共用 fixtures 在 `tests/conftest.py`
- **i18n 一致性**：`tests/test_i18n_consistency.py` 校验 zh/en key 不漂移；新增 i18n key 时双语都要补全
