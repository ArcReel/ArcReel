# 运行测试

改代码或测试后，开发循环先跑与改动相关的最小测试集；任务完成和 push 前，再跑 `AGENTS.md` 中受影响域的完整闸门。

## 选择相关测试

选择结果为 0 个测试时，扩大到对应目录或完整测试集；0 个测试不算验证通过。

- **后端测试文件变更**：直接运行这些文件。
- **后端源码变更**：运行 `tests/unit|integration/` 下的镜像路径及已知消费方。用路径缩小收集范围；`unit` / `integration` / `uses_db` marker 只用于跨路径筛选，`-k` 只用于人工按名称定位。
- **需要后端完整测试的改动**：`pyproject.toml`、`uv.lock`、根 `tests/conftest.py`、含行为的包初始化，或涉及 Alembic、profile、DB、i18n、共享测试设施；无法判断影响范围时也运行完整测试。
- **前端测试文件变更**：直接把文件传给 Vitest。
- **前端源码变更**：在 `frontend/` 运行 `pnpm exec vitest related --run <源文件>`；分支级检查用 `pnpm exec vitest run --changed <base>`。TypeScript 源码变更同时运行完整 typecheck。
- **需要前端完整测试（`pnpm check`）的改动**：`package.json`、`pnpm-lock.yaml`、`vitest.config.*`、测试 setup、i18n 或 branding。
- **子包 `packages/arcreel-market-core/` 源码变更**：运行子包全部测试（`cd packages/arcreel-market-core && uv run python -m pytest`，子包自带 pytest 配置，与主仓分开收集），并按主仓的导入方选择相关测试；子包的消息键增删时同时运行 `tests/unit/lib/i18n/`。

## 在 worktree 里运行

worktree 没有 `.venv` 与 `node_modules`：后端先 `uv sync`，前端与文档站在各自目录 `pnpm install --frozen-lockfile`。标了 `local_port` 的用例在禁止绑定本地端口的沙箱里会被跳过并注明原因，完整测试需要在允许绑定端口的环境里运行一次。
