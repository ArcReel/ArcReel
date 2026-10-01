# ArcReel

AI 视频创作平台，将小说、剧本或创作构想转化为短视频。三层结构：`frontend/`（React SPA）→ `server/`（FastAPI，`agent_runtime/` 封装 Claude Agent SDK）→ `lib/`（核心库）。内嵌创作 Agent 的配置源在 `agent_runtime_profile/`，与开发态 `.claude/` 分离。市场源工具与端点定义校验器是 uv workspace 子包 `packages/arcreel-market-core/`（`arcreel_market_core`），位于 `lib/` 之下且不依赖主仓。

## 工具链与校验

后端使用 `uv`，前端与文档站使用 `pnpm`。修改代码或测试时，先按 `docs/agents/testing.md` 选择并运行相关测试；任务完成和 push 前执行受影响域的完整闸门：

```bash
uv run ruff check . && uv run ruff format . && uv run basedpyright --warnings && uv run lint-imports && uv run deptry lib server alembic scripts tests && uv run python -m pytest -n 4 --dist loadfile
(cd packages/arcreel-market-core && uv run deptry src tests && uv run python -m pytest)   # 改动 packages/arcreel-market-core/ 时
uv run python scripts/audit_tests.py --check   # 改动测试文件时
uv run python scripts/audit_conventions.py --check   # 改动 docs/standards/、依赖清单、.pre-commit-config.yaml、.github/ 或新增豁免注释时
uv run pre-commit run --all-files actionlint && uv run pre-commit run --all-files zizmor   # 改动 .github/ 时
(cd frontend && pnpm check)
(cd website && pnpm check)
```

相关测试必须实际运行且通过。新增或升级依赖：`docs/agents/dependencies.md`。启动开发服务器、数据库迁移、分支与提交规范：`CONTRIBUTING.md`。

## Code Review Rules

写代码或审查 diff 时，按 `CODING_STANDARDS.md` 的索引读取改动路径命中的规范；审查时引用「文件 + 规则标题」报告违规。

## 架构

架构总览、扩展新供应商、扩展新工作流阶段：`website/docs/dev/architecture.md`。

## Agent skills

- 议题追踪：GitHub Issues，用 `gh` CLI 操作；Spec 与 ticket 的约定见 `docs/agents/issue-tracker.md`。
- Triage 标签状态机：`docs/agents/triage-labels.md`。
- 领域文档（`CONTEXT.md` + `docs/adr/`）的使用方式：`docs/agents/domain.md`。
- 项目 schema 迁移：新增或修改 `lib/project/project_migrations/` 的迁移步、改动产物补录规划器时读 `docs/agents/project-migrations.md`。
