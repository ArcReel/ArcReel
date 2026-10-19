# Coding Standards

ArcReel 的代码规范：审查 diff 时需要判断、工具替代不了的项目约定，用来拦住 AI 反复犯的错。正文按领域分文件放在 `docs/standards/`。

## 使用方式

1. 列出 diff 中已改动的文件路径，对照下表，读路径命中的每一份规范；没有命中的不读。
2. 每份规范里，三级标题就是规则本身，正文给出原因、违反的代价与放行条件。报告违规时引用「文件 + 规则标题」。
3. lint、类型检查、`scripts/audit_tests.py` 与 `scripts/audit_conventions.py` 已强制的内容不写进规范，审查时也不重复报告。

<!-- standards-index:start -->
| 规范 | 适用路径 |
|---|---|
| [`docs/standards/backend.md`](docs/standards/backend.md) | `lib/**`, `server/**`, `scripts/**`, `alembic/**`, `packages/**` |
| [`docs/standards/docs.md`](docs/standards/docs.md) | `website/docs/**`, `README.md`, `README.en.md`, `CONTRIBUTING.md` |
| [`docs/standards/frontend.md`](docs/standards/frontend.md) | `frontend/src/**` |
| [`docs/standards/general.md`](docs/standards/general.md) | `**` |
| [`docs/standards/providers.md`](docs/standards/providers.md) | `lib/backends/**`, `lib/custom_provider/**`, `lib/config/**`, `lib/billing/**`, `lib/agent/agent_provider_catalog.py`, `lib/prompts/prompt_builders*.py`, `agent_runtime_profile/**`, `docs/api-docs/**` |
| [`docs/standards/testing.md`](docs/standards/testing.md) | `tests/**`, `packages/*/tests/**`, `frontend/src/**/*.test.*`, `frontend/src/test/**`, `frontend/src/__mocks__/**`, `frontend/e2e/**` |
<!-- standards-index:end -->

## 维护

规范和检查没拦住的错，经收尾自查提交为教训候选，由周巡检记入 `docs/LESSONS.md` 并按阈值晋升；记录、计数、晋升与退出规则见 [`docs/agents/lessons.md`](docs/agents/lessons.md)。新增、修改或删除规则时按以下判据：

- **放在哪里**：按 [retro](.agents/skills/retro/SKILL.md) 的分类决定载体，机械可判的做成 lint 规则、测试或 `scripts/audit_conventions.py` 中的检查。按返工代价划分实现期与审查期：做错后返工代价高的知识（选错层或结构，例如会话调用经 SessionActor 串行、写入后刷新经 `refreshAfterWrite`、可编辑内容接入 `useEditUnit`）写进架构或参考文档，由导航指针在实现期读到；返工代价低的写进规范，在审查期读到。
- **该不该存在**：反事实检验。只有去掉这条规则后 reviewer 会漏掉一类真实发生、并且会再发生的问题，这条规则才有存在的理由。
- **怎么拆分**：只在适用路径差别明显时拆分规范文件。拆分的唯一收益是 reviewer 只读命中的部分。

适用路径以各文件 frontmatter 的 `paths` 为准。上表和 `.coderabbit.yaml` 中的路径映射都由 `uv run python scripts/audit_conventions.py --fix` 生成，CI 中校验它们与 frontmatter 一致。
