# 贡献指南

欢迎贡献代码、报告 Bug 或提出功能建议。

以推广某项商业服务为主要目的的贡献（例如新增某家服务的接入，或在文档中加入服务推荐与链接）不走 PR 流程，这类 PR 会被关闭；合作请联系 support@arc-reel.com。

ArcReel 假定贡献者用 coding Agent 开发。仓库根的 `AGENTS.md`（`CLAUDE.md` 是指向它的链接）是 Agent 的入口，闸门命令、测试选择与代码规范都从它链接出去；本文只讲贡献者本人需要知道的部分。

## 本地开发环境

```bash
# 前置要求：Python 3.12+, Node.js 24.12.0+, uv, pnpm（ffmpeg 随 Python 依赖 imageio-ffmpeg 安装）
# 文档站 website/ 的 Node 版本固定于 website/.node-version
# 操作系统：Linux / macOS / Windows WSL2；Windows 原生可运行项目创建与基础流程，
# Agent 沙箱在 Windows 上降级为命令前缀白名单（见 docs/adr/0025），生产部署推荐 WSL2/Docker

# 安装依赖
uv sync
cd frontend && pnpm install && cd ..

# 一次性安装 pre-commit 钩子（ruff / eslint / actionlint / zizmor）
uv run pre-commit install

# 初始化数据库
uv run alembic upgrade head

# 启动后端 (终端 1)
# 注意：必须用 --reload-dir 限定监视目录，否则 watchfiles 会扫描
# node_modules / .venv / .git / .worktrees 等数十万个文件，单核 CPU 占用超过 50%
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241

# 启动前端 (终端 2)
cd frontend && pnpm dev

# 访问 http://localhost:5173
```

### 文档站

`website/` 是独立包根，有独立的 lockfile，不与 frontend 组成 workspace：

```bash
cd website && pnpm install

pnpm start        # 开发预览
pnpm build        # 双 locale 构建，失效链接或锚点会导致构建失败
pnpm typecheck
pnpm lint         # ESLint
pnpm format       # prettier 写入；format:check 仅校验不修改
pnpm check        # sync-contributing + typecheck + lint + format:check + check-consistency；不含 build 与 scripts/*.test.mjs

# 站内搜索仅在构建产物上可用，dev server 中不可用
pnpm build && pnpm serve

# 将仓库根 CONTRIBUTING.md 同步为开发区页面（start / build 已自动前置执行，通常无需手动运行）
pnpm sync-contributing

# CI 一致性检查：页面清单 / 孤立译文 / 上站文档标题缺少显式锚点 / UI JSON key 完整性，任一不满足即非零退出；
# 依赖 sync-contributing 的产物，须先运行 sync-contributing
pnpm check-consistency
```

## 测试

开发循环与 push 前的完整闸门见 [`AGENTS.md`](https://github.com/ArcReel/ArcReel/blob/main/AGENTS.md)「工具链与校验」和 [`docs/agents/testing.md`](https://github.com/ArcReel/ArcReel/blob/main/docs/agents/testing.md)；CI 跑同一套检查。

### 前端页面级测试

`frontend/e2e/` 是 Playwright 页面级套件。它在 1024×600 到 2560×1440 的五个视口上打开各页面区域，检查文档本身不滚动、没有被裁切又无法滚动到达的内容、纵向滚动区（含弹层正文）没有被撑出横向滚动，并用 axe 按 WCAG 2.2 AA 检查可访问性。CI 的 `frontend-e2e` job 在官方 Playwright 镜像里运行。本地用 run-server 远程模式在同一镜像里渲染，结果与 CI 一致：

```bash
cd frontend
pnpm e2e:server   # 终端一：在 Docker 里启动 Playwright run-server，镜像版本取自 @playwright/test
pnpm e2e:remote   # 终端二：构建前端，连接容器里的浏览器运行套件
```

没有 Docker 时，先运行 `pnpm exec playwright install chromium`，再运行 `pnpm e2e`。这种方式在本机渲染，字体与布局可能和 CI 不同，结果以容器内为准。

同一台机器上并行运行多份套件时，用环境变量错开端口与产物目录，不设置时取括号内的默认值：`E2E_PORT` 是预览服务端口（4173），`E2E_OUTPUT_DIR` 是失败截图与 trace 的目录（`frontend/test-results`），`E2E_SERVER_PORT` 是 run-server 映射到本机的端口（3000），`pnpm e2e:server` 与 `pnpm e2e:remote` 读同一个变量。`CI=1` 时生成的 HTML 报告用 Playwright 自带的 `PLAYWRIGHT_HTML_OUTPUT_DIR` 改目录。截图基线按默认端口生成，换端口后页面上显示本机地址的截图不参与比对。

- **新增场景**：在 `frontend/e2e/regions/` 下用 `defineRegionScenarios` 登记，写明打开的路由、就绪条件和要探测的状态。场景以会改变可用高度的状态为主，例如展开面板、打开弹层。
- **豁免**：装饰性的裁切层用 `data-overflow-ok="原因"` 豁免，属性值必须写明原因。确需横向滚动的区域（时间线轨道、代码块、标签带）写 `overflow-x-auto`，探针按这个类名识别，不需要另加豁免。
- **接口数据替身**：`frontend/e2e/fixtures/recorded/` 由 `pnpm e2e:record` 对真实后端录制。脚本需要 `uv`，会在临时数据目录里启动后端并创建演示项目。后端改动接口形状的 PR 同时重录，形状变化体现为替身文件的 diff。长文本、多条目等压力变体写在场景的 `api` 字段里。
- **页面截图**：场景登记 `screenshot` 后，设置 `E2E_SCREENSHOTS=1` 运行才会比对，只在 1024×600、1440×900、2560×1440 三个视口拍。CI 的 `frontend-e2e` 设置了这个变量，截图出现差异或缺少基线时 job 失败。基线只能在容器里生成和比对：用 `pnpm e2e:server` 与 `pnpm e2e:remote` 运行，新增或有意改动的截图加 `--update-snapshots=changed` 重新生成，逐张核对后提交。本机渲染的字体与容器不同，不要用它生成基线。

## 代码质量

代码规范的入口是 [`CODING_STANDARDS.md`](https://github.com/ArcReel/ArcReel/blob/main/CODING_STANDARDS.md)：按改动路径索引到 `docs/standards/` 下的领域规范，只收工具替代不了、需要判断的项目约定。本地审查和 PR 上的 AI reviewer 都以它为依据。

### 依赖管理

新增或升级依赖的步骤见 [`docs/agents/dependencies.md`](https://github.com/ArcReel/ArcReel/blob/main/docs/agents/dependencies.md)。

## 文档维护

用户文档的唯一发布位置是 [docs.arc-reel.com](https://docs.arc-reel.com)，源文件在 `website/docs/`（本地构建与预览见上文「文档站」）。中文是唯一写作源，英文译文由 AI 生成，人工仅审校中文源。内部文档（ADR、`CONTEXT.md`、`AGENTS.md`、安全威胁模型、供应商 API 文档索引等）不上站，留在仓库 `docs/` 下；`SECURITY.md` 因 GitHub Security 选项卡依赖也留在仓库根。

本文件是贡献指南的真相源，构建时复制为站点的开发区页面（`website/scripts/sync-contributing.mjs`），中文副本不入库。

### 各页职责

上站页面另在 frontmatter 用 `update_docs` 声明文档刷新流程的覆盖档位，判据见 `.agents/skills/update-docs/SKILL.md`。

| 页面 | 应该包含 | 不应该包含 |
|---|---|---|
| `README.md` | 产品定位、核心价值、最短上手路径 | 完整模型清单、所有环境变量、内部实现细节 |
| `website/docs/index.mdx` | 文档站定位、主要入口和导航概览 | 具体功能的完整操作步骤 |
| `website/docs/guide/getting-started.md` | 从部署到第一条成片的完整操作路径 | 生产级反向代理和备份策略 |
| `website/docs/guide/workflows.md` | 创作类型、生成模式、内容确认节点、选择建议 | 供应商密钥和运维命令 |
| `website/docs/guide/providers.md` | 供应商类型、覆盖能力、选择原则、配置层级 | 容易过期的价格承诺 |
| `website/docs/guide/comfyui.md` | ComfyUI workflow 的导入、节点绑定、尺寸/时长/种子换算语义、测试与运行限制 | ComfyUI 自身的部署方式与自定义节点安装教程 |
| `website/docs/guide/market.md` | 市场源管理、市场条目的安装确认、更新与卸载 | 投稿流程与开设市场源的步骤（以官方市场源仓库文档为准） |
| `website/docs/guide/jianying-export.md` | 剪映草稿目录定位、导出与二次编辑操作步骤 | 视频生成本身的流程说明 |
| `website/docs/guide/faq.md` | 高频问题和短答案 | 长篇教程 |
| `website/docs/ops/deployment.md` | 部署、升级、备份、恢复、监控和安全 | 产品营销文案 |
| `website/docs/ops/migrate-to-postgres.md` | SQLite 到 PostgreSQL 的迁移步骤、校验和回滚 | PostgreSQL 的日常部署与运维手册 |
| `website/docs/dev/architecture.md` | 稳定的架构边界、数据流和扩展点 | 临时实现计划和未完成设计 |
| `SECURITY.md` | 支持版本、支持的部署边界、私密漏洞报告和协调披露政策 | 未修复漏洞细节和动态风险登记 |
| `docs/security/threat-model.md` | 安全资产、信任边界、攻击面、现有控制和重评触发条件 | 可直接利用的未修复漏洞与补丁历史 |

### 写作约定

上站页面与 README 的写作规范见 [`docs/standards/docs.md`](https://github.com/ArcReel/ArcReel/blob/main/docs/standards/docs.md)；功能变更需要同步哪些文档见 [`docs/standards/general.md`](https://github.com/ArcReel/ArcReel/blob/main/docs/standards/general.md)「功能变更与对应文档在同一个 PR 里」。上站页面的每个标题写成 `## 标题 {#english-id}`，中英两个 locale 共用同一锚点。

## 工作流程

### 分支策略（trunk-based）

- 只有 `main` 是长期分支。所有工作从最新 `main` 切短分支完成，PR 合回 `main`
- 禁止直接 push 到 `main`。个人分支同样经 PR 流程合并，提交前自行检查 diff 与验收清单

### 分支命名约定

`<type>/<slug>`，`type` 取 conventional commit 类型之一：

AFK 团队流程的短期运行分支例外使用 `afk/<batch-id>/stage-<K>` 与 `issue/<N>`。

- `feat/` — 新功能（如 `feat/reference-video-backend`）
- `fix/` — Bug 修复（如 `fix/queue-lease-timeout`）
- `refactor/` — 重构（如 `refactor/session-actor`）
- `docs/` — 纯文档（如 `docs/contribution-infra`）
- `chore/` — 构建/工具 / 版本号 / 清理（如 `chore/freeze-versions`）
- `ci/` — CI 配置（如 `ci/testing-discipline`）
- `test/` — 仅测试

`slug` 用小写 + 短横线，简短描述该分支的主题。

### 短分支寿命

从创建到合并 ≤ 3 天。超期应拆分或先 rebase 主线同步，避免将长期分支直接提交 review。

### Squash merge

每个 PR 压缩为 1 个 commit 合并回 `main`，commit message 遵循 conventional commits 规范（见下节）。GitHub 上选择 "Squash and merge"。

`afk-team-workflow` 生成的 stage PR 是例外：它用 "Rebase and merge" 保留每个 issue 的 conventional commit；清尾与 review loop 产生的非 issue commits 在合并前压成一个 conventional integration-fix commit。

## 提交规范

Commit message 采用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
feat: 新增功能描述
fix: 修复问题描述
refactor: 重构描述
docs: 文档变更
chore: 构建/工具变更
```

标题格式为 `type(scope): 摘要`，scope 可省略。squash 合并下 PR 标题即 changelog 条目：描述用户可感知的收益，范围词使用产品术语而非实现术语（status_code、内部类名等），并如实限定范围。type 取值与 changelog 分类见下文「发版流程」与 `.release-please-config.json`。

## 发版流程

版本号与 changelog 由 [release-please](https://github.com/googleapis/release-please) 自动维护（配置见 `.release-please-config.json`，workflow 见 `.github/workflows/release-please.yml`）。**开发者无需手动 bump 版本号**——只需写合规的 conventional commits。

### 工作流程

1. 普通 PR 按 conventional commits 规范 squash merge 到 `main`；`afk-team-workflow` stage PR 按上述例外 rebase merge
2. release-please 扫描自上次 release 以来的 commit，自动创建或更新标题形如 `chore(main): release X.Y.Z` 的 Release PR，包含下一版本号与更新后的 `CHANGELOG.md`
3. 合并该 Release PR 即自动创建 `vX.Y.Z` tag 并发布 GitHub Release

### commit type → 版本步进

| commit type | 版本步进 | changelog |
|-------------|---------|-----------|
| `feat`      | minor   | ✨ 新功能 |
| `fix`       | patch   | 🐛 Bug 修复 |
| `feat!` / 任意 type + `!` / footer 含 `BREAKING CHANGE:` | **major**（版本 <1.0.0 时为 minor） | ⚠️ BREAKING CHANGES（changelog 置顶） |
| `perf` / `refactor` / `docs` / `revert` | 不步进 | 显示（⚡ / ♻️ / 📚 / ↩️） |
| `chore` / `ci` / `build` / `test` / `style` | 不步进 | 隐藏 |

> release-please 默认只有 `feat` 和 `fix`（以及破坏性变更）触发版本 bump。将 `perf`/`refactor`/`docs`/`revert` 配置为 `hidden: false` 仅影响 changelog 呈现，不会使它们触发 patch bump。如果一轮迭代只有这几类 commit，不会产出 Release PR，直到下一个 `fix`/`feat` commit 到来。

`pyproject.toml` 和 `frontend/package.json` 的 `version` 字段由 release-please 自动维护（见 `pyproject.toml` 的 `# managed by release-please` 注释），**开发者视为只读**。`uv.lock` 同样由 release-please workflow 在 Release PR 分支上自动 `uv lock` 同步。实际版本状态以 git tag + `.release-please-manifest.json` 为准。

### commit 示例

```
# 新功能（minor bump）
feat(image-backends): 支持 OpenAI DALL-E 3 后端

# Bug 修复（patch bump）
fix(queue): 修复任务 lease 超时后未正确归还的问题

# 带 scope 与正文
feat(grid): 支持 grid_12 布局

将多宫格分镜系统扩展到 12 宫格，适用于长篇剧集的批量预览。
```

**本仓库不使用破坏性变更标记。** 前后端同仓一体发布，后端 API 不做版本化对外承诺——自带前端随版本同步演进，外部集成通过 `/agent-installation-guide.md` 获取当前安装入口、不依赖版本号。接口删改按 `fix`/`refactor` 正常分类，不加 `!` 后缀、不写 `BREAKING CHANGE:` footer。误标合并后的纠正按 merge 方式处理：普通 squash PR 编辑正文追加 `BEGIN_COMMIT_OVERRIDE`/`END_COMMIT_OVERRIDE` 块，等待下一次 main push 或手动重跑 workflow；AFK rebase stage 则在最后一次 main push 更新 Release PR 后，直接校正其版本与 changelog 产物并通过完整性校验，再合并 Release PR。0.x 阶段的 `bump-minor-pre-major` 仅把误标的版本跃迁限制为 minor，不修正 changelog。

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
- 在 changelog 顶部插入独立的 **⚠️ BREAKING CHANGES** 区块，汇总展示每条破坏性变更的描述
- 在对应 type section（如 `✨ 新功能`）下保留该 commit 的常规条目
