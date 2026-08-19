# ArcReel

AI 视频创作平台：把小说、剧本或创作构想转成短视频。三层：`frontend/`（React SPA）→ `server/`（FastAPI，`agent_runtime/` 封装 Claude Agent SDK）→ `lib/`（核心库）。内嵌创作智能体的配置源在 `agent_runtime_profile/`，与开发态 `.claude/` 分离。

## 工具链与校验

后端用 `uv`，前端与文档站用 `pnpm`。push 前按改动范围跑通：

```bash
uv run ruff check . && uv run ruff format . && uv run basedpyright && uv run lint-imports && uv run python -m pytest
cd frontend && pnpm lint && pnpm check
cd website && pnpm check
```

起服务、数据库迁移、分支与提交规范、依赖管理、注释纪律见 `CONTRIBUTING.md`。

## 通用纪律

- 面向用户的文本同时补齐全部已支持语言的翻译 key（语言清单以 `frontend/src/i18n/` 为准，`tests/test_i18n_consistency.py` 把关）。
- 代码与测试注释只写当下行为与约束；变更缘由与议题编号写进 commit message / PR 描述。

## 架构

架构总览、扩展新供应商、扩展新工作流阶段：`website/docs/dev/architecture.md`。

## Agent skills

### Issue tracker

议题（issue/Spec）追踪在 `ArcReel/ArcReel` 的 GitHub Issues，统一用 `gh` CLI 操作。Spec 用 `Spec` 标签 + `Spec:` 标题前缀；细分 issue 标题尾缀 `[Spec #N]` 并挂原生 sub-issue。详见 `docs/agents/issue-tracker.md`。

### Triage labels

triage 状态机使用五个默认标签：`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`，另有 `parked` 标记刻意搁置的 issue。详见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文布局：根目录 `CONTEXT.md` + `docs/adr/`。详见 `docs/agents/domain.md`。
