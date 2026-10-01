# Coding Standards

ArcReel 的代码规范：写代码与审查代码时需要判断、工具替代不了的项目约定。正文按领域分文件放在 `docs/standards/`。

## 使用方式

1. 列出要改动或 diff 中已改动的文件路径，对照下表，读路径命中的每一份规范；没有命中的不读。
2. 每份规范里，三级标题就是规则本身，正文给出原因、违反的代价与放行条件。报告违规时引用「文件 + 规则标题」。
3. lint、类型检查、`scripts/audit_tests.py` 与 `scripts/audit_conventions.py` 已强制的内容不写进规范，审查时也不重复报告。

<!-- standards-index:start -->
| 规范 | 适用路径 |
|---|---|
| [`docs/standards/agent-runtime.md`](docs/standards/agent-runtime.md) | `server/agent_runtime/**`, `server/agent_toolset/**`, `lib/agent/**`, `agent_runtime_profile/**` |
| [`docs/standards/backend.md`](docs/standards/backend.md) | `lib/**`, `server/**`, `scripts/**`, `alembic/**`, `packages/**` |
| [`docs/standards/docs.md`](docs/standards/docs.md) | `website/docs/**`, `README.md`, `README.en.md`, `CONTRIBUTING.md` |
| [`docs/standards/frontend-async.md`](docs/standards/frontend-async.md) | `frontend/src/**` |
| [`docs/standards/frontend-ui.md`](docs/standards/frontend-ui.md) | `frontend/src/**` |
| [`docs/standards/general.md`](docs/standards/general.md) | `**` |
| [`docs/standards/providers.md`](docs/standards/providers.md) | `lib/backends/**`, `lib/custom_provider/**`, `lib/config/**`, `lib/billing/**`, `lib/agent/agent_provider_catalog.py`, `lib/prompts/prompt_builders*.py`, `agent_runtime_profile/**`, `docs/api-docs/**` |
| [`docs/standards/testing.md`](docs/standards/testing.md) | `tests/**`, `packages/*/tests/**`, `frontend/src/**/*.test.*`, `frontend/src/test/**`, `frontend/src/__mocks__/**` |
| [`docs/standards/windows.md`](docs/standards/windows.md) | `lib/**`, `server/**`, `packages/*/src/**` |
<!-- standards-index:end -->

## 维护

审查漏掉一类问题时，先判断它是否机械可判：可判的做成 lint 规则、测试或 `scripts/audit_conventions.py` 中的检查；只有需要判断的才写成规则，放进路径对应的规范文件。适用路径以各文件 frontmatter 的 `paths` 为准。上表和 `.coderabbit.yaml` 中的路径映射都由 `uv run python scripts/audit_conventions.py --fix` 生成，CI 中校验它们与 frontmatter 一致。
