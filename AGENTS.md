# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Language Policy
- **All responses to users must be in English**: all replies, task lists, and planning documents must use English.

## Project Overview

ArcReel is an AI video generation platform that converts novels into short videos. Three-layer architecture:

```
frontend/ (React SPA)  →  server/ (FastAPI)  →  lib/ (core library)
  React 19 + Tailwind       routing + SSE          Gemini API
  wouter routing            agent_runtime/         GenerationQueue
  zustand state mgmt        (Claude Agent SDK)     ProjectManager
```

## Development Commands

```bash
# Backend
uv run python -m pytest                              # tests (-v single file / -k keyword / --cov coverage)
uv run ruff check . && uv run ruff format .          # lint + format
uv sync                                              # install dependencies
uv run alembic upgrade head                          # database migration
uv run alembic revision --autogenerate -m "desc"     # generate migration

# Frontend (cd frontend &&)
pnpm build       # production build (includes typecheck)
pnpm check       # typecheck + test
```

## Architecture Notes

### Backend API Routes

All APIs are under `/api/v1`, routes defined in `server/routers/`:
- `projects.py` — project CRUD, overview generation
- `generate.py` — storyboard/video/character/clue generation (enqueued to task queue)
- `assistant.py` — Claude Agent SDK session management (SSE streaming)
- `agent_chat.py` — agent conversation interaction
- `tasks.py` — task queue status (SSE streaming)
- `project_events.py` — project event SSE push
- `files.py` — file upload and static assets
- `versions.py` — asset version history and rollback
- `characters.py` / `clues.py` — character/clue management
- `usage.py` — API usage statistics
- `cost_estimation.py` — cost estimation (project/episode/scene)
- `auth.py` / `api_keys.py` — authentication and API key management
- `system_config.py` — system configuration
- `providers.py` — preset provider configuration management (list, read/write, connection test)
- `custom_providers.py` — custom provider CRUD, model management and discovery, connection test

### server/services/ — Business Service Layer

- `generation_tasks.py` — storyboard/video/character/clue generation task orchestration
- `project_archive.py` — project export (ZIP packaging)
- `project_events.py` — project change event publishing
- `jianying_draft_service.py` — CapCut draft export
- `cost_estimation.py` — cost estimation calculation and actual cost aggregation

### lib/ Core Modules

- **{gemini,ark,grok,openai}_shared** — per-provider SDK factories and shared utilities
- **image_backends/** / **video_backends/** / **text_backends/** — multi-provider media generation backends, Registry + Factory pattern (gemini/ark/grok/openai)
- **custom_provider/** — custom provider support: backend wrapper, model discovery, factory creation (OpenAI/Google compatible)
- **MediaGenerator** (`media_generator.py`) — combines backend + VersionManager + UsageTracker
- **GenerationQueue** (`generation_queue.py`) — async task queue, SQLAlchemy ORM backend, lease-based concurrency control
- **GenerationWorker** (`generation_worker.py`) — background worker, separate image/video concurrency channels
- **ProjectManager** (`project_manager.py`) — project filesystem operations and data management
- **StatusCalculator** (`status_calculator.py`) — computes status fields on read, does not store redundant state
- **UsageTracker** (`usage_tracker.py`) — API usage tracking
- **CostCalculator** (`cost_calculator.py`) — cost calculation
- **TextGenerator** (`text_generator.py`) — text generation tasks
- **retry** (`retry.py`) — generic exponential backoff retry decorator, reused across provider backends

### lib/config/ — Provider Configuration System

ConfigService (`service.py`) → Repository (persistence + key masking) → Resolver (resolution). `registry.py` maintains the preset provider registry (PROVIDER_REGISTRY).

### lib/db/ — SQLAlchemy Async ORM Layer

- `engine.py` — async engine + session factory (`DATABASE_URL` defaults to `sqlite+aiosqlite`)
- `models/` — ORM models: Task / ApiCall / ApiKey / AgentSession / Config / Credential / User / CustomProvider / CustomProviderModel
- `repositories/` — async repositories: Task / Usage / Session / ApiKey / Credential / CustomProvider

Database file: `projects/.arcreel.db` (development SQLite)

### Agent Runtime (Claude Agent SDK Integration)

`server/agent_runtime/` wraps the Claude Agent SDK:
- `AssistantService` (`service.py`) — orchestrates Claude SDK sessions
- `SessionManager` — session lifecycle + SSE subscriber pattern
- `StreamProjector` — builds real-time assistant replies from streaming events

### Frontend

- React 19 + TypeScript + Tailwind CSS 4
- Routing: `wouter` (not React Router)
- State management: `zustand` (stores in `frontend/src/stores/`)
- Path alias: `@/` → `frontend/src/`
- Vite proxy: `/api` → `http://127.0.0.1:1241`

## Key Design Patterns

### Data Layering

| Data Type | Storage Location | Strategy |
|---------|---------|------|
| Character/clue definitions | `project.json` | Single source of truth; scripts only reference names |
| Episode metadata (episode/title/script_file) | `project.json` | Written synchronously when script is saved |
| Stat fields (scenes_count / status / progress) | Not stored | `StatusCalculator` computes and injects on read |

### Real-time Communication

- Assistant: `/api/v1/assistant/sessions/{id}/stream` — SSE streaming replies
- Project events: `/api/v1/projects/{name}/events/stream` — SSE push for project changes
- Task queue: frontend polls `/api/v1/tasks` for status

### Task Queue

All generation tasks (storyboard/video/character/clue) are uniformly enqueued via GenerationQueue and processed asynchronously by GenerationWorker.
`generation_queue_client.py`'s `enqueue_and_wait()` wraps enqueue + wait for completion.

### Pydantic Data Models

`lib/script_models.py` defines `NarrationSegment` and `DramaScene` for script validation.
`lib/data_validator.py` validates the structure and reference integrity of `project.json` and episode JSON files.

## Agent Runtime Environment

Agent-specific configuration (skills, agents, system prompt) lives in `agent_runtime_profile/`,
physically separated from the development-time `.claude/` directory.

### Skill Maintenance

```bash
# Trigger rate evaluation (requires anthropic SDK: uv pip install anthropic)
PYTHONPATH=~/.claude/plugins/cache/claude-plugins-official/skill-creator/*/skills/skill-creator:$PYTHONPATH \
  uv run python -m scripts.run_eval \
  --eval-set <eval-set.json> \
  --skill-path agent_runtime_profile/.claude/skills/<skill-name> \
  --model sonnet --runs-per-query 2 --verbose
```

#### Gotchas

- **SKILL.md and script must stay in sync**: when modifying a skill script, update SKILL.md accordingly, and vice versa — the two must always be consistent.

## Environment Setup

Copy `.env.example` to `.env` and set authentication parameters (`AUTH_USERNAME`/`AUTH_PASSWORD`/`AUTH_TOKEN_SECRET`).
API keys, backend selection, model configuration, etc. are managed via the WebUI settings page (`/settings`).
External tool dependency: `ffmpeg` (video concatenation and post-processing).

### Code Quality

**ruff** (lint + format):
- Rule sets: `E`/`F`/`I`/`UP`, ignoring `E402` (existing pattern) and `E501` (managed by formatter)
- line-length: 120
- Excludes `.worktrees` and `.claude/worktrees` directories
- Enforced in CI: `ruff check . && ruff format --check .`

**pytest**:
- `asyncio_mode = "auto"` (no need to manually mark async tests)
- Coverage scope: `lib/` and `server/`, CI requires ≥80%
- Shared fixtures in `tests/conftest.py`, factories in `tests/factories.py`, fakes in `tests/fakes.py`
- Test dependencies in `[dependency-groups] dev`; installed by default with `uv sync`; excluded in production images via `--no-dev`
