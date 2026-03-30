# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 语言规范
- **回答用户必须使用中文**：所有回复、思考过程、任务清单及计划文件，均须使用中文

## 项目概述

ArcReel 是一个 AI 视频生成平台，将小说转化为短视频。三层架构：

```
frontend/ (React SPA)  →  server/ (FastAPI)  →  lib/ (核心库)
  React 19 + Tailwind       路由分发 + SSE        Gemini API
  wouter 路由               agent_runtime/        GenerationQueue
  zustand 状态管理          (Claude Agent SDK)     ProjectManager
```

## 开发命令

```bash
# 后端
uv run uvicorn server.app:app --reload --port 1241   # 启动开发服务器
uv run python -m pytest                                # 全部测试
uv run python -m pytest tests/test_generation_queue.py -v  # 单文件
uv run python -m pytest -k "test_enqueue" -v           # 按关键字
uv run python -m pytest --cov --cov-report=html        # 覆盖率
uv run ruff check .                                    # lint 检查
uv run ruff format .                                   # 代码格式化
uv sync                                                # 安装依赖
uv run alembic upgrade head                            # 数据库迁移
uv run alembic revision --autogenerate -m "desc"       # 生成迁移

# 前端
cd frontend && pnpm dev                                # 开发服务器 (5173，代理 /api → 1241)
cd frontend && pnpm build                              # 生产构建 (含 typecheck)
cd frontend && pnpm test                               # vitest 测试
cd frontend && pnpm typecheck                          # TypeScript 类型检查
cd frontend && pnpm check                              # typecheck + test
cd frontend && pnpm test:watch                         # vitest watch 模式
```

## 架构要点

### 后端 API 路由

所有 API 在 `/api/v1` 下，路由定义在 `server/routers/`：
- `projects.py` — 项目 CRUD、概述生成
- `generate.py` — 分镜/视频/角色/线索生成（入队到任务队列）
- `assistant.py` — Claude Agent SDK 会话管理（SSE 流式）
- `agent_chat.py` — 智能体对话交互
- `tasks.py` — 任务队列状态（SSE 流式）
- `project_events.py` — 项目事件 SSE 推送
- `files.py` — 文件上传与静态资源
- `versions.py` — 资源版本历史与回滚
- `characters.py` / `clues.py` — 角色/线索管理
- `usage.py` — API 用量统计
- `auth.py` / `api_keys.py` — 认证与 API 密钥管理
- `system_config.py` — 系统配置
- `providers.py` — 供应商配置管理（列表、读写、连接测试）

### lib/ 核心模块

- **gemini_shared** (`gemini_shared.py`) — 共享工具（RateLimiter、重试装饰器、Vertex AI scopes）
- **image_backends/** — 多供应商图片生成后端（gemini/ark/grok），Registry 模式
- **video_backends/** — 多供应商视频生成后端（gemini/ark/grok），Registry 模式
- **MediaGenerator** (`media_generator.py`) — 组合后端 + VersionManager + UsageTracker
- **GenerationQueue** (`generation_queue.py`) — 异步任务队列，SQLAlchemy ORM 后端，lease-based 并发控制
- **GenerationWorker** (`generation_worker.py`) — 后台 Worker，分 image/video 两条并发通道
- **ProjectManager** (`project_manager.py`) — 项目文件系统操作和数据管理
- **StatusCalculator** (`status_calculator.py`) — 读时计算状态字段，不存储冗余状态
- **UsageTracker** (`usage_tracker.py`) — API 用量追踪
- **CostCalculator** (`cost_calculator.py`) — 费用计算
- **TextGenerator** (`text_generator.py`) — 文本生成任务

### lib/config/ — 供应商配置系统

- `registry.py` — 供应商注册表（PROVIDER_REGISTRY）
- `service.py` — ConfigService，供应商配置读写
- `repository.py` — 配置持久化 + 密钥脱敏
- `resolver.py` — 配置解析

### lib/db/ — SQLAlchemy Async ORM 层

- `engine.py` — 异步引擎 + session factory；`DATABASE_URL` 环境变量控制后端（默认 `sqlite+aiosqlite`）
- `base.py` — `DeclarativeBase`
- `models/` — ORM 模型：`Task`、`ApiCall`、`ApiKey`、`AgentSession`、`Config`、`Credential`、`User`
- `repositories/` — 异步 Repository：`TaskRepository`、`UsageRepository`、`SessionRepository`、`ApiKeyRepository`、`CredentialRepository`

数据库文件：`projects/.arcreel.db`（开发 SQLite）

### Agent Runtime（Claude Agent SDK 集成）

`server/agent_runtime/` 封装 Claude Agent SDK：
- `AssistantService` (`service.py`) — 编排 Claude SDK 会话
- `SessionManager` — 会话生命周期 + SSE 订阅者模式
- `StreamProjector` — 从流式事件构建实时助手回复

### 前端

- React 19 + TypeScript + Tailwind CSS 4
- 路由：`wouter`（非 React Router）
- 状态管理：`zustand`（stores 在 `frontend/src/stores/`）
- 路径别名：`@/` → `frontend/src/`
- Vite 代理：`/api` → `http://127.0.0.1:1241`

## 关键设计模式

### 数据分层：写时同步 vs 读时计算

- 角色/线索**定义**只存 `project.json`，剧本中仅引用**名称**
- `scenes_count`、`status`、`progress` 等统计字段由 `StatusCalculator` 读时注入，永不存储
- 剧集元数据（episode/title/script_file）在剧本保存时写时同步

### 实时通信

- 助手：`/api/v1/assistant/sessions/{id}/stream` — SSE 流式回复
- 项目事件：`/api/v1/projects/{name}/events/stream` — SSE 推送项目变更
- 任务队列：前端轮询 `/api/v1/tasks` 获取状态

### 任务队列

所有生成任务（分镜/视频/角色/线索）统一通过 GenerationQueue 入队，由 GenerationWorker 异步处理。
`generation_queue_client.py` 的 `enqueue_and_wait()` 封装入队 + 等待完成。

### Pydantic 数据模型

`lib/script_models.py` 定义 `NarrationSegment` 和 `DramaScene`，用于剧本验证。
`lib/data_validator.py` 验证 `project.json` 和剧集 JSON 的结构与引用完整性。

## 智能体运行环境

智能体专用配置（skills、agents、系统 prompt）位于 `agent_runtime_profile/` 目录，
与开发态 `.claude/` 物理分离。

### Skill 维护

```bash
# 触发率评估（需要 anthropic SDK：uv pip install anthropic）
PYTHONPATH=~/.claude/plugins/cache/claude-plugins-official/skill-creator/*/skills/skill-creator:$PYTHONPATH \
  uv run python -m scripts.run_eval \
  --eval-set <eval-set.json> \
  --skill-path agent_runtime_profile/.claude/skills/<skill-name> \
  --model sonnet --runs-per-query 2 --verbose
```

#### Gotchas

- **SKILL.md 是规格文档**：compose-video 等 skill 的 SKILL.md 描述的 CLI 可能超前于脚本实现（如 `--episode`、`--fallback-mode` 等），修改脚本时需对照 SKILL.md 补齐
- **触发率测试的局限**：`run_eval.py` 用 `claude -p` 跑独立查询（无对话历史），依赖上下文的短指令（如"继续"、"下一步"）在隔离测试中必然失败，不代表实际触发率
- **CLI 接口一致性**：generate-characters 和 generate-clues 的脚本现在都支持 `--all`/`--list`/`--character|--clue` 三种模式，新增资产类 skill 应遵循此模式

## 环境配置

复制 `.env.example` 到 `.env`，设置认证参数（`AUTH_USERNAME`/`AUTH_PASSWORD`/`AUTH_TOKEN_SECRET`）。
API Key、后端选择、模型配置等通过 WebUI 配置页（`/settings`）管理。
外部工具依赖：`ffmpeg`（视频拼接与后期处理）。

### 代码质量

**ruff**（lint + format）：
- 规则集：`E`/`F`/`I`/`UP`，忽略 `E402`（既有模式）和 `E501`（由 formatter 管理）
- line-length：120
- 排除 `.worktrees`、`.claude/worktrees` 目录
- CI 中强制检查：`ruff check . && ruff format --check .`

**pytest**：
- `asyncio_mode = "auto"`（无需手动标记 async 测试）
- 测试覆盖范围：`lib/` 和 `server/`，CI 要求 ≥80%
- 共用 fixtures 在 `tests/conftest.py`，工厂在 `tests/factories.py`，fakes 在 `tests/fakes.py`
- test 依赖在 `[dependency-groups] dev` 中，`uv sync` 默认安装，生产镜像通过 `--no-dev` 排除
