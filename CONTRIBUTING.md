# 贡献指南

欢迎贡献代码、报告 Bug 或提出功能建议！

## 本地开发环境

```bash
# 前置要求：Python 3.12+, Node.js 20+, uv, pnpm, ffmpeg
# 操作系统：Linux / MacOS / Windows WSL2（Windows 原生不支持）

# 安装依赖
uv sync
cd frontend && pnpm install && cd ..

# 一次性安装 pre-commit 钩子（ruff / eslint / pull_request_target tripwire）
uv run pre-commit install

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

版本号与 changelog 由 [release-please](https://github.com/googleapis/release-please) 自动维护（配置见 `.release-please-config.json`，workflow 见 `.github/workflows/release-please.yml`）。**开发者无需手动 bump 版本号**——只需写合规的 conventional commits。

### 工作流程

1. PR 按 conventional commits 规范 squash merge 到 `main`
2. release-please 扫描自上次 release 以来的 commit，自动开/更新一个标题形如 `chore(main): release X.Y.Z` 的 Release PR，里面包含下次版本号 bump + 更新的 `CHANGELOG.md`
3. 合并该 Release PR 即自动打 `vX.Y.Z` tag 并发布 GitHub Release

### commit type → 版本步进

| commit type | 版本步进 | changelog |
|-------------|---------|-----------|
| `feat`      | minor   | ✨ 新功能 |
| `fix`       | patch   | 🐛 Bug 修复 |
| `feat!` / 任意 type + `!` / footer 含 `BREAKING CHANGE:` | **major** | ⚠️ BREAKING CHANGES（changelog 置顶） |
| `perf` / `refactor` / `docs` / `revert` | 不步进 | 显示（⚡ / ♻️ / 📚 / ↩️） |
| `chore` / `ci` / `build` / `test` / `style` | 不步进 | 隐藏 |

> release-please 默认只有 `feat` 和 `fix`（以及破坏性变更）触发版本 bump。把 `perf`/`refactor`/`docs`/`revert` 配成 `hidden: false` 只影响 changelog 呈现，不会使它们触发 patch bump。如果一轮迭代只有这几类 commit，不会产出 Release PR，直到下一个 `fix`/`feat` commit 到来。

`pyproject.toml` 和 `frontend/package.json` 的 `version` 字段由 release-please 自动维护（见 `pyproject.toml` 的 `# managed by release-please` 注释），**开发者视为只读**。`uv.lock` 同样由 release-please workflow 在 Release PR 分支上自动 `uv lock` 同步。实际版本状态以 git tag + `.release-please-manifest.json` 为准。

### commit 示例

```
# 新功能（minor bump）
feat(image-backends): 支持 OpenAI DALL-E 3 后端

# Bug 修复（patch bump）
fix(queue): 修复任务 lease 超时后未正确归还的问题

# 带 scope 与正文
feat(grid): 支持 grid_12 布局

将宫格系统扩展到 12 宫格，适用于长篇剧集的批量预览。
```

**破坏性变更**有两种等价写法，release-please 均会自动 bump 到 major：

```
# 写法 1：type 后加 !
feat(api)!: 移除 /api/v1/legacy 端点

# 写法 2：footer 含 BREAKING CHANGE（更常用，可以写多行说明）
feat(auth): 统一 API Key 验证逻辑

BREAKING CHANGE: /api/v1/api-keys 的返回结构改为 { items: [...] }，
旧客户端需要适配。
```

两种写法 release-please 都会：
- 将版本号 bump 为 major
- 在 changelog 顶部插入独立的 **⚠️ BREAKING CHANGES** 区块，把每条破坏性变更的描述汇总展示
- 在对应 type section（如 `✨ 新功能`）下保留该 commit 的常规条目
