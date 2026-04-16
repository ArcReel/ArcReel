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

### ESLint disable 使用规范

本项目在 PR 3（#219）后采用零 warning 政策，所有规则均为 error。如必须绕过，遵循：

- **形式**：`// eslint-disable-next-line <rule> -- <中文理由>`，`--` 后的理由**强制**
- **禁用**：文件级 `/* eslint-disable */`、无理由的 `// eslint-disable-line`、`@ts-ignore` 联用
- **PR 描述要求**：新增的 disable 必须在 PR body 以表格列出 `rule | file:line | 理由`
- **文件级关闭**只允许通过 `eslint.config.js` 的 `files` override，且须在 config 注释说明原因
- **不可接受的理由**：「太麻烦」「暂时这样」「later fix」
- **可接受的理由示例**：「React setter 引用稳定」「mount-only 初始化」「生成式预览视频无字幕源」

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

### Pytest markers 纪律

新增测试必须按类型打标，默认 CI 跑 `-m "not e2e"`：

| Marker | 含义 | 禁止 |
|--------|------|------|
| `unit` | 快速、隔离，不碰真实 I/O / 外部服务 | — |
| `integration` | 跨模块协作，使用真实依赖（in-memory DB、tmp 文件系统等） | **禁止 mock 被测 module 的公共入口**（例如测 `MediaGenerator` 的集成测试不能 mock `MediaGenerator.generate`，否则是在测 mock 本身） |
| `e2e` | 端到端，依赖真实外部资源（远程 API、大模型调用、真实 ffmpeg 重活） | CI 默认跳过，本地按需运行 |

现存测试不强制回溯打标；只对新增测试落实。

## 工作流程

### 分支策略（trunk-based）

- 只有 `main` 是长期分支。所有工作从最新 `main` 切短分支完成，PR 合回 `main`
- 禁止 `git push origin main` 直推。即使个人分支也走 PR 流程，自己先过一遍 diff + 验收清单

### 分支命名约定

`<type>/<slug>`，`type` 取 conventional commit 类型之一：

- `feat/` — 新功能（如 `feat/reference-video-backend`）
- `fix/` — Bug 修复（如 `fix/queue-lease-timeout`）
- `refactor/` — 重构（如 `refactor/session-actor`）
- `docs/` — 纯文档（如 `docs/contribution-infra`）
- `chore/` — 构建/工具 / 版本号 / 清理（如 `chore/freeze-versions`）
- `ci/` — CI 配置（如 `ci/testing-discipline`）
- `test/` — 仅测试

`slug` 用小写 + 短横线，简短描述该分支聚焦点。

### 短分支寿命

从创建到合并 ≤ 3 天。超期要么拆分，要么先 rebase 主线同步——**不要**把 1 个月的分支直接拖进 review。

### Squash merge

每个 PR 压成 1 个 commit 合回 `main`，commit message 用 conventional commits 规范（见下节）。GitHub 上 merge 按钮选 "Squash and merge"。

### 已知 defer 的处理

PR 模板有"已知 defer"一节。合入前必须把每一条**开成 follow-up issue** 并把链接填进 PR description；不允许以"之后再说"的形式遗留。

## 提交规范

Commit message 采用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
feat: 新增功能描述
fix: 修复问题描述
refactor: 重构描述
docs: 文档变更
chore: 构建/工具变更
```

## 发版流程

当前形态：手动维护版本号。

- 修改 `pyproject.toml` 和 `frontend/package.json` 的 `version` 字段到同一值
- `uv lock` 同步 `uv.lock`
- 提交 `chore: bump version to X.Y.Z`，打 tag `vX.Y.Z`，推送

后续将由 release-please 接管（PR 追加），届时开发者仅需写合规 conventional commits，版本号与 changelog 自动生成。本节届时重写。
