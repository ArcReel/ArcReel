# ArcReel

AI 视频创作平台，将小说、剧本或创作构想转化为短视频。三层结构：`frontend/`（React SPA）→ `server/`（FastAPI，`agent_runtime/` 封装 Claude Agent SDK）→ `lib/`（核心库）。内嵌创作 Agent 的配置源在 `agent_runtime_profile/`，与开发态 `.claude/` 分离。

## 工具链与校验

后端使用 `uv`，前端与文档站使用 `pnpm`。push 前按改动范围执行对应校验：

```bash
uv run ruff check . && uv run ruff format . && uv run basedpyright && uv run lint-imports && uv run python -m pytest
uv run python scripts/audit_tests.py --check   # 改动测试文件时；同时扫后端 tests/ 与前端 *.test.*
(cd frontend && pnpm lint && pnpm check)
(cd website && pnpm check)
```

启动开发服务器、数据库迁移、测试规范（分层/替身/判据/闸门）、分支与提交规范、依赖管理、注释规范见 `CONTRIBUTING.md`。

## 通用规范

- 面向用户的文本须同步添加全部已支持语言的翻译 key（语言清单以 `frontend/src/i18n/` 为准，由 `tests/unit/lib/i18n/test_i18n_consistency.py` 校验）。
- 代码与测试注释仅描述当前行为与约束；变更原因与议题编号写在 commit message / PR 描述中。

## 架构

架构总览、扩展新供应商、扩展新工作流阶段：`website/docs/dev/architecture.md`。

## Agent skills

- 议题追踪：GitHub Issues，用 `gh` CLI 操作；Spec 与细分 issue 的约定见 `docs/agents/issue-tracker.md`。
- Triage 标签状态机：`docs/agents/triage-labels.md`。
- 领域文档（`CONTEXT.md` + `docs/adr/`）的使用方式：`docs/agents/domain.md`。
