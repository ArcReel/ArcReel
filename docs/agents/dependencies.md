# 新增与升级依赖

1. 由包管理器写入版本与锁文件。后端用 `uv add <包>`，开发依赖加 `--group dev`。前端和文档站在各自目录用 `pnpm add <包>`。
2. 在 [Dependabot 配置](../../.github/dependabot.yml)中，将新增的直接依赖归入对应生态的主题分组。运行 `uv run python scripts/audit_conventions.py --check` 会检出仅落入 `all-other` 的直接依赖。
3. 主仓运行 `uv run deptry lib server alembic scripts tests`，涉及市场或目录子包时在子包目录另跑 `uv run deptry src tests`。直接 import 的第三方包需要声明，间接加载的运行时插件按 deptry 约定登记。

## 已知版本约束

- **TypeScript** 同时受 `typescript-eslint` peer 范围及文档站继承的类型配置约束。升级时核对锁定版本和 [Dependabot 的 website 段](../../.github/dependabot.yml)。
- **后端 `anthropic` SDK 锁定在 0.x。** 其 1.x 版本的 HTTP 层改用了 `httpx2`，而测试用的 respx（`tests/http_capture.py`）仅能拦截 `httpx`，调用端点在 HTTP transport 层改写请求路径的实现也基于 `httpx`，两者均无法适配 1.x。解锁条件：respx 支持 `httpx2`，且仓库内 Python 调用端点共用的 HTTP 客户端与 transport 整体迁至 `httpx2`（`openai` 等 SDK 同步迁移）。
- **`pyproject.toml` 与 `frontend/package.json` 的 `version` 字段由 release-please 维护**，视为只读。
- **shadcn 原语需随依赖同步升级。** 单纯升级依赖包不会重新生成 `frontend/src/components/ui/*.tsx`。大版本（major）升级应单独开 PR，并在同一 PR 中对已安装组件运行 `pnpm exec shadcn add <组件> --diff`，手动合并上游改动，同时保留有意的本地修改。遇到原语缺陷时，也先用 `add --diff` 排查上游是否已修复。
- **`playwright-core` 由 override 跟随 `@playwright/test` 的版本。** `frontend/package.json` 的 `"playwright-core": "$@playwright/test"` 让 `@axe-core/playwright` 的 peer 与 `react-doctor` 等间接依赖都解析到同一份 `playwright-core`。去掉它后，间接依赖可能拉入更新的 `playwright-core`，e2e 中会出现两套 `Page` 类型，`pnpm typecheck` 报 `TS2739`。升级 `@playwright/test` 时 override 自动跟随，无需改动。
- **供应商图标只使用 `@lobehub/icons/es/*/components/{Color,Mono}` 的独立 SVG 入口。** `frontend/package.json` 的定向 override 排除该包的 `@lobehub/ui` peer；这些入口只依赖 React，不使用其 UI 组件，避免自动引入另一套 UI 与动效依赖。若改用 Avatar、组合入口或其他依赖 `@lobehub/ui` 的组件，先复核此约束。
