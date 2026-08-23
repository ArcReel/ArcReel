# ArcReel

## 开发任务启动闸门

任何新的 feature、bug、重构或文档改动，在执行代码或文档检索、Graphify 查询、创建 worktree 或其他仓库操作前，必须先完整阅读 [`DEV.md`](DEV.md)。`DEV.md` 是需求确认、worktree、验证、commit、merge 与清理流程的唯一权威来源；本文件只保留项目架构和代码约束，不复制开发流程。

AI 视频创作平台，将小说、剧本或创作构想转化为短视频。三层结构：`frontend/`（React SPA）→ `server/`（FastAPI，`agent_runtime/` 封装 Claude Agent SDK）→ `lib/`（核心库）。内嵌创作 Agent 的配置源在 `agent_runtime_profile/`，与开发态 `.claude/` 分离。

## 工具链与校验

后端使用 `uv`，前端与文档站使用 `pnpm`。开发中先跑改动相关测试，任务完成后再对受影响域做一次全量；不要在每个小改动后重复执行全仓测试：

```bash
# 开发反馈环：预览后执行改动相关测试
uv run python scripts/test_changed.py --base main
uv run python scripts/test_changed.py --base main --run

# 任务完成闸门：只对本任务触达的域各跑一次全量
uv run ruff check . && uv run ruff format --check . && uv run basedpyright && uv run lint-imports
uv run python -m pytest -m "not e2e"
(cd frontend && pnpm check)
(cd website && pnpm check)
```

完整的 Related / Domain full / Repository full 时机与命令见 `DEV.md`；测试规范（分层/替身/判据/闸门）、分支与提交规范、依赖管理、注释规范见 `CONTRIBUTING.md`。

## 通用规范

- 面向用户的文本须同步添加全部已支持语言的翻译 key（语言清单以 `frontend/src/i18n/` 为准，由 `tests/test_i18n_consistency.py` 校验）。
- 代码与测试注释仅描述当前行为与约束；变更原因与议题编号写在 commit message / PR 描述中。

## 架构

架构总览、扩展新供应商、扩展新工作流阶段：`website/docs/dev/architecture.md`。

## Agent skills

- 议题追踪：GitHub Issues，用 `gh` CLI 操作；Spec 与细分 issue 的约定见 `docs/agents/issue-tracker.md`。
- Triage 标签状态机：`docs/agents/triage-labels.md`。
- 领域文档（`CONTEXT.md` + `docs/adr/`）的使用方式：`docs/agents/domain.md`。

## 开发与维护流程

Feature、bug、重构和文档改动的 worktree、验证、commit、merge、清理以及 Graphify 更新流程统一见启动闸门指定的 [`DEV.md`](DEV.md)。

### 最优开发 / 修复原则

- **最优实现，而非最小 diff**：目标是做「能满足需求的最小完整功能实现」，不是「改动行数最少」。
- **拒绝补丁式修改**：不在既有代码上打补丁堆屎山；正确实现需要重构时就重构，让结构先正确。
- **重构同样最优化**：重构只做必要范围，不过度重构——以「最优的修复与开发」为准，而不是以「改动最小」为准。
