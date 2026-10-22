# ArcReel

AI 视频创作平台，将小说、剧本或创作构想转化为短视频。三层结构：`frontend/`（React SPA）→ `server/`（FastAPI，`agent_runtime/` 封装 Claude Agent SDK）→ `lib/`（核心库）。内嵌创作 Agent 的配置源在 `agent_runtime_profile/`，与开发态 `.claude/` 分离。uv workspace 子包位于 `lib/` 之下且不依赖主仓：市场源工具与端点定义校验器在 `packages/arcreel-market-core/`（`arcreel_market_core`），模型目录数据的格式与校验器在 `packages/arcreel-catalog-core/`（`arcreel_catalog_core`）。

## 工具链与校验

后端使用 `uv`，前端与文档站使用 `pnpm`。修改代码或测试时，先按 `docs/agents/testing.md` 选择并运行相关测试；push 前跑完整闸门，团队流程里由负责 push 的角色跑：

```bash
uv run python scripts/gate.py --changed            # 按相对 origin/main 的改动选域，一次跑完
uv run python scripts/gate.py backend frontend     # 或直接点名域；各域的触发路径与步骤见 --list
```

另一份闸门正在跑时它会排队，等待是正常现象。相关测试必须实际运行且通过。提 PR 前，检查本次是否出过值得改进环境的错，判据与记录方式见 `docs/agents/lessons.md`「收尾自查」。新增或升级依赖：`docs/agents/dependencies.md`。启动开发服务器、数据库迁移、分支与提交规范：`CONTRIBUTING.md`。

## Code Review Rules

审查 diff 时，按 `CODING_STANDARDS.md` 的索引读取改动路径命中的规范，引用「文件 + 规则标题」报告违规。

## 架构

架构总览、扩展新供应商、扩展新工作流阶段：`website/docs/dev/architecture.md`。

## Agent skills

- 找用户：向用户提问或请示前，以及处置超出当前范围的发现时，读 `docs/agents/escalation.md`。
- 议题追踪：GitHub Issues，用 `gh` CLI 操作；Spec 与 ticket 的约定见 `docs/agents/issue-tracker.md`。
- Triage 标签状态机：`docs/agents/triage-labels.md`。
- 领域文档（`GLOSSARY.md` + `docs/adr/`）的使用方式：`docs/agents/domain.md`。
- 项目 schema 迁移：新增或修改 `lib/project/project_migrations/` 的迁移步、改动产物补录规划器时读 `docs/agents/project-migrations.md`。
