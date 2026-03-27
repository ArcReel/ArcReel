# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

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
python -m pytest                                       # 全部测试
python -m pytest tests/test_generation_queue.py -v     # 单文件
python -m pytest -k "test_enqueue" -v                  # 按关键字
python -m pytest --cov --cov-report=html               # 覆盖率
uv sync                                                # 安装依赖
uv run alembic upgrade head                            # 数据库迁移
uv run alembic revision --autogenerate -m "desc"       # 生成迁移

# 前端
cd frontend && pnpm dev                                # 开发服务器 (5173，代理 /api → 1241)
cd frontend && pnpm build                              # 生产构建 (含 typecheck)
cd frontend && pnpm test                               # vitest 测试
cd frontend && pnpm typecheck                          # TypeScript 类型检查
cd frontend && pnpm check                              # typecheck + test
```

## 架构要点

### 后端 API 路由

所有 API 在 `/api/v1` 下，路由定义在 `server/routers/`：
- `projects.py` — 项目 CRUD、概述生成
- `generate.py` — 分镜/视频/角色/线索生成（入队到任务队列）
- `assistant.py` — Claude Agent SDK 会话管理（SSE 流式）
- `tasks.py` — 任务队列状态（SSE 流式）
- `files.py` — 文件上传与静态资源
- `versions.py` — 资源版本历史与回滚
- `characters.py` / `clues.py` — 角色/线索管理
- `usage.py` — API 用量统计

### lib/ 核心模块

- **GeminiClient** (`gemini_client.py`) — Gemini API 统一封装，含速率限制和重试
- **MediaGenerator** (`media_generator.py`) — 组合 GeminiClient + VersionManager + UsageTracker
- **GenerationQueue** (`generation_queue.py`) — 异步任务队列，SQLAlchemy ORM 后端，lease-based 并发控制
- **GenerationWorker** (`generation_worker.py`) — 后台 Worker，分 image/video 两条并发通道
- **ProjectManager** (`project_manager.py`) — 项目文件系统操作和数据管理
- **StatusCalculator** (`status_calculator.py`) — 读时计算状态字段，不存储冗余状态

### lib/db/ — SQLAlchemy Async ORM 层

- `engine.py` — 异步引擎 + session factory；`DATABASE_URL` 环境变量控制后端（默认 `sqlite+aiosqlite`）
- `base.py` — `DeclarativeBase`
- `models/` — ORM 模型：`Task`、`TaskEvent`、`WorkerLease`、`ApiCall`、`AgentSession`
- `repositories/` — 异步 Repository：`TaskRepository`、`UsageRepository`、`SessionRepository`

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

## 环境配置

复制 `.env.example` 到 `.env`，设置 `GEMINI_API_KEY` 和 `ANTHROPIC_API_KEY`。
外部工具依赖：`ffmpeg`（视频拼接与后期处理）。

### pytest 配置

- `asyncio_mode = "auto"`（无需手动标记 async 测试）
- 测试覆盖范围：`lib/` 和 `server/`
- 共用 fixtures 在 `tests/conftest.py`，工厂在 `tests/factories.py`，fakes 在 `tests/fakes.py`
