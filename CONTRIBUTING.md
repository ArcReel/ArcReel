# 贡献指南

欢迎贡献代码、报告 Bug 或提出功能建议！

## 本地开发环境

```bash
# 前置要求：Python 3.12+, Node.js 20+, uv, pnpm, ffmpeg
# 文档站 website/ 另需 Node 24（版本钉在 website/.node-version）
# 操作系统：Linux / MacOS / Windows WSL2（Windows 原生不支持）

# 安装依赖
uv sync
cd frontend && pnpm install && cd ..

# 一次性安装 pre-commit 钩子（ruff / eslint / pull_request_target tripwire）
uv run pre-commit install

# 初始化数据库
uv run alembic upgrade head

# 启动后端 (终端 1)
# 注意：必须用 --reload-dir 限定监视目录，否则 watchfiles 会扫描
# node_modules / .venv / .git / .worktrees 等十几万个文件，单核 CPU 50%+
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241

# 启动前端 (终端 2)
cd frontend && pnpm dev

# 访问 http://localhost:5173
```

### 文档站

`website/` 是独立包根，有自己的 lockfile，不与 frontend 组 workspace：

```bash
cd website && pnpm install

pnpm start        # 开发预览
pnpm build        # 双 locale 构建，broken link / anchor 直接 fail
pnpm typecheck
pnpm lint         # ESLint
pnpm format       # prettier 写回；format:check 只校验不改写
pnpm check        # typecheck + lint + format:check，等价于 CI 的三道静态闸

# 站内搜索只在构建产物上生效，dev server 里不工作
pnpm build && pnpm serve

# 把仓库根 CONTRIBUTING.md 同步为开发区页面（start / build 已自动前置执行，一般无需手动跑）
pnpm sync-contributing

# CI 一致性闸门：页面库存 / 孤儿译文 / 上站文档标题缺显式锚点 / UI JSON key 齐全性，任一命中非零退出；
# 依赖 sync-contributing 已同步过的产物，须先跑 sync-contributing 再跑本命令
pnpm check-consistency
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

**Lint & Format（文档站 ESLint + prettier）：**

```bash
cd website && pnpm check          # typecheck + lint + format:check
cd website && pnpm lint:fix       # ESLint 自动修可修的部分
cd website && pnpm format         # prettier 写回
```

- 配置：`website/eslint.config.mjs` + `website/.prettierrc.json`（`website/` 是独立包根，工具链与 `frontend/` 各自独立，因为两者的 TypeScript 大版本不同）
- ESLint 规则集与 frontend 同构：`typescript-eslint/recommendedTypeChecked` + `react/recommended` + `react-hooks/recommended` + `jsx-a11y/recommended`
- prettier printWidth 120（与后端 ruff 的 line-length 对齐）；`docs/` 与 `i18n/` 不参与格式化，排除依据见 `website/.prettierignore` 顶部注释
- CI 中强制检查：`website-checks` job 的 `Typecheck` / `Lint` / `Format check` 三个 step，均排在 `Build` 之前

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

- TypeScript 版本锁：`typescript-eslint@8.x` 的 peer 范围为 `typescript <6.1`；升 TS 到 6.1+ 前需同步升级 `typescript-eslint`

**测试覆盖率：**

- CI 要求 ≥80%
- `asyncio_mode = "auto"`（无需手动标记 async 测试）

### Pytest markers 纪律

每个测试用例必须恰好带一个类型标记，默认 CI 跑 `-m "not e2e"`：

| Marker | 含义 | 禁止 |
|--------|------|------|
| `unit` | 快速、隔离，不碰真实 I/O / 外部服务 | — |
| `integration` | 跨模块协作，使用真实依赖（in-memory DB、tmp 文件系统等） | **禁止 mock 被测 module 的公共入口**（例如测 `MediaGenerator` 的集成测试不能 mock `MediaGenerator.generate`，否则是在测 mock 本身） |
| `e2e` | 端到端，依赖真实外部资源（远程 API、大模型调用、真实 ffmpeg 重活） | CI 默认跳过，本地按需运行 |

标记可打在用例、类或模块（`pytestmark`）任一层，三层叠加后仍须恰好命中一个分类。

打标由 pytest 收集期强制，不依赖人工 review：

- 漏标或多标的用例让收集直接失败（`tests/conftest.py::_enforce_classification_markers`），报错列出具体 nodeid
- `--strict-markers` 使未在 `pyproject.toml` 注册的 marker 同样在收集期失败

`unit`/`integration` 的现存分类由批量默认档得出（真实调用 ffmpeg 生成测试用音视频资源、`uses_db` 命中的归 `integration`，其余归 `unit`），不保证逐条语义精确；新增测试按上表语义自行选择——用真实 ffmpeg 生成用例夹具与 `e2e` 定义的"真实 ffmpeg 重活"不是同一回事：前者是调用 ffmpeg 产出测试输入，后者指端到端场景里的重量级 ffmpeg 处理链路。

## 文档维护

用户文档的唯一发布位置是 [docs.arc-reel.com](https://docs.arc-reel.com)，源文件在 `website/docs/`（本地构建与预览见上文「文档站」）。中文是唯一写作源，英文译文由 AI 生成、人工只审中文源。内部文档（ADR、`CONTEXT.md`、`AGENTS.md`、安全威胁模型、供应商 API 文档索引等）不上站，留在仓库 `docs/` 下；`SECURITY.md` 因 GitHub Security 选项卡依赖也留在仓库根。

本文件是贡献指南的真相源，构建时复制为站点的开发区页面（`website/scripts/sync-contributing.mjs`），中文副本不入库。

### 各页职责

| 页面 | 应该包含 | 不应该包含 |
|---|---|---|
| `README.md` | 产品定位、核心价值、最短上手路径 | 完整模型清单、所有环境变量、内部实现细节 |
| `website/docs/index.mdx` | 文档站定位、主要入口和导航概览 | 具体功能的完整操作步骤 |
| `website/docs/guide/getting-started.md` | 从部署到第一条成片的完整操作路径 | 生产级反向代理和备份策略 |
| `website/docs/guide/workflows.md` | 内容模式、视频制作方式、审核节点、选择建议 | 供应商密钥和运维命令 |
| `website/docs/guide/providers.md` | 供应商类型、覆盖能力、选择原则、配置层级 | 容易过期的价格承诺 |
| `website/docs/guide/jianying-export.md` | 剪映草稿目录定位、导出与二次编辑操作步骤 | 视频生成本身的流程说明 |
| `website/docs/guide/faq.md` | 高频问题和短答案 | 长篇教程 |
| `website/docs/ops/deployment.md` | 部署、升级、备份、恢复、监控和安全 | 产品营销文案 |
| `website/docs/ops/migrate-to-postgres.md` | SQLite 到 PostgreSQL 的迁移步骤、校验和回滚 | PostgreSQL 的日常部署与运维手册 |
| `website/docs/ops/migrate-to-mysql.md` | SQLite / PostgreSQL 到 MySQL 的迁移步骤、校验和回滚 | MySQL 的日常部署与运维手册 |
| `website/docs/dev/architecture.md` | 稳定的架构边界、数据流和扩展点 | 临时实现计划和未完成设计 |
| `SECURITY.md` | 支持版本、支持的部署边界、私密漏洞报告和协调披露政策 | 未修复漏洞细节和动态风险登记 |
| `docs/security/threat-model.md` | 安全资产、信任边界、攻击面、现有控制和重评触发条件 | 可直接利用的未修复漏洞与补丁历史 |

### 写作约定

- **README 保持稳定**：README 只需让第一次访问仓库的人回答「ArcReel 是什么、适不适合我、和直接调模型 API 有什么区别、怎样最快跑起来」。具体模型名称、单价和接口参数放到站点对应页面，避免供应商每次更新都要重写首页。
- **供应商信息以运行时能力为准**：文档描述覆盖哪些媒体类型、ArcReel 如何统一配置、不同能力如何选择、具体信息在哪里确认；设置页中实际可选的模型与供应商官方文档是最终依据。
- **标题带显式锚点 ID**：上站页面的每个标题写成 `## 标题 {#english-id}`，中英两个 locale 共用同一锚点，避免中文自动 slug 随文案改动而失效。站内互引用相对文件路径（如 `../ops/deployment.md`），指向未上站的仓库文件时用 GitHub 绝对链接。
- **文档变更应与功能变更一起提交**：新增内容模式或视频制作方式、新增供应商或媒体能力、部署目录/端口/环境变量变化、数据目录/备份方式/迁移行为变化、对外 API/许可证或商业使用方式变化，均须同步更新对应文档。
- **上站 `.md` 不能使用 JSX / import**：`website/docusaurus.config.ts` 设 `markdown.format: "detect"`，`.md` 按 CommonMark 解析而非 MDX：两者都不会报编译错误，但也都不会按 MDX 语法执行——JSX 标签被当作原始 HTML 原样输出（带子内容的标签，子内容会直接显示成页面文本），import 语句被当作普通文本原样显示。需要 JSX 的页面改用 `.mdx`。

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
| `feat!` / 任意 type + `!` / footer 含 `BREAKING CHANGE:` | **major**（版本 <1.0.0 时为 minor） | ⚠️ BREAKING CHANGES（changelog 置顶） |
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

**本仓库不使用破坏性变更标记。** 前后端同仓一体发布，后端 API 不做版本化对外承诺——自带前端随版本同步演进，外部集成（OpenClaw 等）经 `/skill.md` 运行时拉取最新契约、不依赖版本号，删改 `public/skill.md.template` 引用的端点时同步更新该模板。接口删改按 `fix`/`refactor` 正常分类，不加 `!` 后缀、不写 `BREAKING CHANGE:` footer。误标合并后的纠正方式：编辑该 PR 正文追加 `BEGIN_COMMIT_OVERRIDE`/`END_COMMIT_OVERRIDE` 块，release-please 按 override 重算 changelog 与版本号（需 squash 合并，本仓库满足）；workflow 仅在 main push 时运行，编辑后需等下一次 main push 或手动重跑 release-please workflow 才生效。0.x 阶段的 `bump-minor-pre-major` 仅把误标的版本跃迁限制为 minor，不修正 changelog。

以下语法说明仅用于识别误标。**破坏性变更**有两种等价写法：

```
# 写法 1：type 后加 !
feat(api)!: 移除 /api/v1/legacy 端点

# 写法 2：footer 含 BREAKING CHANGE（更常用，可以写多行说明）
feat(auth): 统一 API Key 验证逻辑

BREAKING CHANGE: /api/v1/api-keys 的返回结构改为 { items: [...] }，
旧客户端需要适配。
```

两种写法 release-please 都会：
- 将版本号 bump 为 major；当前版本 <1.0.0 时受 `bump-minor-pre-major` 配置约束，只 bump minor
- 在 changelog 顶部插入独立的 **⚠️ BREAKING CHANGES** 区块，把每条破坏性变更的描述汇总展示
- 在对应 type section（如 `✨ 新功能`）下保留该 commit 的常规条目
