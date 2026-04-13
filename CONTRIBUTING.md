# 贡献指南

欢迎贡献代码、报告 Bug 或提出功能建议！

## 本地开发环境

```bash
# 前置要求：Python 3.12+, Node.js 20+, uv, pnpm, ffmpeg
# 操作系统：Linux / MacOS / Windows WSL2（Windows 原生不支持）

# 安装依赖
uv sync
cd frontend && pnpm install && cd ..

# 初始化数据库
uv run alembic upgrade head

# 启动后端 (终端 1)
uv run uvicorn server.app:app --reload --port 1241

# 启动前端 (终端 2)
cd frontend && pnpm dev

# 访问 http://localhost:5173
```

## 运行测试

```bash
# 后端测试
python -m pytest

# 前端类型检查 + 测试
cd frontend && pnpm check
```

## 代码质量

**Lint & Format（ruff）：**

```bash
uv run ruff check . && uv run ruff format .
```

- 规则集：`E`/`F`/`I`/`UP`，忽略 `E402` 和 `E501`
- line-length：120
- CI 中强制检查：`ruff check . && ruff format --check .`

**Lint（前端 ESLint）：**

```bash
cd frontend && pnpm lint          # 检查
cd frontend && pnpm lint:fix      # 自动修可修的部分
```

- 配置：`frontend/eslint.config.js`（flat config）
- 规则集：`typescript-eslint/recommendedTypeChecked` + `react/recommended` + `react-hooks/recommended` + `jsx-a11y/recommended`
- typed linting 启用 `projectService: true`，能检查 `no-floating-promises`、`no-misused-promises` 等 async 相关问题
- CI 中强制检查：`frontend-tests` job 的 `Lint` step

**baseline ratchet（--max-warnings）：**

项目处于 a11y 工程化迁移期。`package.json` 的 `"lint"` 脚本里 `--max-warnings=<N>` 锁住历史未修的 warning 总数：

- N > 0 时，CI 只允许 warning ≤ N（新增 warning 会失败）
- 修复 warning 后须**同步下调** N 数字；不允许上调
- 修完一类 rule 后，从 `eslint.config.js` 里对应的 `MIGRATION_WARN_RULES_*` 常量中删除该 rule 条目（rule 自动升回 recommended 预设的 error 级）
- 目标：N 最终降到 0，移除 `--max-warnings` 参数

**eslint.config.js 里有三个迁移常量**，按 rule 类型分组：

| 常量名 | 作用域 | 包含的 rule 类型 |
|--------|-------|--------------------|
| `MIGRATION_WARN_RULES_TYPED` | `src/**/*.{ts,tsx}` 非 test | 需要 TypeScript 类型信息的 rule（`@typescript-eslint/no-floating-promises` / `no-misused-promises` / `no-unsafe-*` 等） |
| `MIGRATION_WARN_RULES_A11Y` | 非 test 文件 | jsx-a11y rule（`jsx-a11y/click-events-have-key-events` 等） |
| `MIGRATION_WARN_RULES_ALL` | 全局 | 其他非 typed 非 a11y rule（`react-hooks/*` / `no-unsafe-finally` / `@typescript-eslint/no-explicit-any` 等） |

**PR 2 / PR 3 作者的操作清单：**

1. 本地 `cd frontend && pnpm lint` 看当前 warning 数
2. 修复 warning 直到数字下降
3. 更新 `package.json` 里 `--max-warnings=<N>` 为当前数字
4. 根据 rule 类型，从 `eslint.config.js` 的对应常量（`MIGRATION_WARN_RULES_TYPED` / `_A11Y` / `_ALL`）中删除已清零的 rule 条目
5. 提交，CI 验证

CR checklist：**`--max-warnings` 数字在 diff 里只允许减不允许加**。

**本地 IDE 建议（不提交 repo）：**

`.vscode/` 已在 `.gitignore`。自行添加 `frontend/.vscode/settings.json` 可让 VS Code / Cursor 实时显示 lint 黄线并在保存时自动修复：

```json
{
  "eslint.workingDirectories": [{ "pattern": "./frontend" }],
  "editor.codeActionsOnSave": { "source.fixAll.eslint": "explicit" }
}
```

**已知约束：**

- ESLint 锁在 v9 系列：`eslint-plugin-react-hooks@7` 的 peer dependency 尚未支持 ESLint v10，待插件更新后独立升级
- TypeScript 版本锁：`typescript-eslint@8.x` 的 peer 范围为 `typescript <6.1`；升 TS 到 6.1+ 前需同步升级 `typescript-eslint`

**测试覆盖率：**

- CI 要求 ≥80%
- `asyncio_mode = "auto"`（无需手动标记 async 测试）

## 提交规范

Commit message 采用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
feat: 新增功能描述
fix: 修复问题描述
refactor: 重构描述
docs: 文档变更
chore: 构建/工具变更
```
