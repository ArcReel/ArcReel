# 贡献指南

欢迎贡献代码、报告 Bug 或提出功能建议。

以推广某项商业服务为主要目的的贡献（例如新增某家服务的接入，或在文档中加入服务推荐与链接）不走 PR 流程，这类 PR 会被关闭；合作请联系 support@arc-reel.com。

带 `ready-for-agent` 标签的 issue 已写好实现说明，由维护方的 coding Agent 实现。外部 PR 不会让它更快完成，只会多一轮审查，这类 PR 会被关闭。

开发新功能或进行较大规模的改动前，建议先提 issue 讨论方案，避免实现后因方向不符而被关闭。

PR 合并前，作者需签署 CLA，签署入口会由 CLA Assistant 自动发布在 PR 评论中。

ArcReel 默认贡献者使用 coding Agent 辅助开发。仓库根目录的 `AGENTS.md`（`CLAUDE.md` 是指向它的链接）是 Agent 的入口，闸门命令、测试选择与代码规范都从这里索引出去；本文只说明贡献者本人需要了解的部分。

## 本地开发环境

```bash
# 前置要求：Python 3.12+, Node.js 24.12.0+, uv, pnpm（ffmpeg 随 Python 依赖 imageio-ffmpeg 安装）
# 文档站 website/ 的 Node 版本固定于 website/.node-version
# 操作系统：Linux / macOS / Windows WSL2；Windows 原生可运行项目创建与基础流程，
# Agent 沙箱在 Windows 上降级为命令前缀白名单（见 docs/adr/0026），生产部署推荐 WSL2/Docker

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
# Windows 原生环境去掉 --reload：uvicorn 启用 reload 时在 Windows 上改用不支持子进程的事件循环，
# 内置 Agent 无法拉起 Claude Code。改代码后手动重启后端
uv run uvicorn server.app:app --port 1241

# 启动前端 (终端 2)
cd frontend && pnpm dev

# 访问 http://localhost:5173
```

### 文档站

文档站位于 `website/`，使用独立的 `pnpm` lockfile，Node.js 版本见 `website/.node-version`。

```bash
cd website
pnpm install --frozen-lockfile
pnpm start                 # 本地预览
pnpm check                 # 类型、格式、翻译同步及站点一致性检查
pnpm build                 # 构建中英文版本并检查链接和锚点
```

站内搜索在构建结果中可用。构建后运行 `pnpm serve` 预览。

## 测试

开发循环与 push 前的完整闸门见 [`AGENTS.md`](https://github.com/ArcReel/ArcReel/blob/main/AGENTS.md) 的「工具链与校验」一节以及 [`docs/agents/testing.md`](https://github.com/ArcReel/ArcReel/blob/main/docs/agents/testing.md)；CI 会运行同一套检查。

### 前端页面级测试

`frontend/e2e/` 是 Playwright 页面级测试套件。它会在 1024×600 到 2560×1440 的五个视口下打开各页面区域，检查文档本身不滚动、没有被裁切且无法滚动到达的内容、纵向滚动区（含弹层正文）不会被撑出横向滚动，并用 axe 按 WCAG 2.2 AA 标准检查可访问性。CI 的 `frontend-e2e` job 在官方 Playwright 镜像里运行，PR 上只跑有截图基线的三个视口（`E2E_VIEWPORTS=screenshot`），每日全量 CI 跑全部五个。本地用 run-server 远程模式在同一镜像里渲染，结果与 CI 一致：

```bash
cd frontend
pnpm e2e:server   # 终端一：在 Docker 里启动 Playwright run-server，镜像版本取自 @playwright/test
pnpm e2e:remote   # 终端二：构建前端，连接容器里的浏览器运行套件
```

如果没有 Docker 环境，可以先运行 `pnpm exec playwright install chromium`，再运行 `pnpm e2e`。这种方式会在本机直接渲染，字体与布局可能与 CI 存在差异，最终结果以容器内为准。

在同一台机器上并行运行多份测试套件时，可以通过环境变量错开端口与产物目录（未设置时采用括号内的默认值）：`E2E_PORT` 为预览服务端口（4173），`E2E_OUTPUT_DIR` 为失败截图与 trace 的输出目录（`frontend/test-results`），`E2E_SERVER_PORT` 为 run-server 映射到本机的端口（3000），`pnpm e2e:server` 与 `pnpm e2e:remote` 会读取同一个变量。`CI=1` 时生成的 HTML 报告可通过 Playwright 自带的 `PLAYWRIGHT_HTML_OUTPUT_DIR` 指定目录。截图基线按默认端口生成，更换端口后，页面中显示本机地址的截图将不参与比对。

- **新增场景**：在 `frontend/e2e/regions/` 下用 `defineRegionScenarios` 登记，写明打开的路由、就绪条件和要探测的状态。场景以会改变可用高度的状态为主，例如展开面板、打开弹层。
- **豁免**：装饰性的裁切层用 `data-overflow-ok="原因"` 豁免，属性值必须写明原因。确需横向滚动的区域（时间线轨道、代码块、标签带）写 `overflow-x-auto`，探针按这个类名识别，不需要另加豁免。
- **接口数据替身**：`frontend/e2e/fixtures/recorded/` 由 `pnpm e2e:record` 对真实后端录制。脚本需要 `uv`，会在临时数据目录里启动后端并创建演示项目。录制后端（`scripts/e2e_record_backend.py`）只读仓库内的输入：目录数据取后端测试共用的 `tests/fixtures/catalog.json`，生命周期判定的「今天」固定为它的数据集日期，应用版本固定，不做线上目录同步，也不读 `.env`。改动后端接口结构或这份测试目录数据的 PR 需同步重录，变更会体现在替身文件的 diff 中。`pnpm e2e:record:check` 再起一个前移系统时钟、换掉出厂快照与版本号的后端，两边各录一次，替身不同即失败；CI 的 `e2e-record-drift` 与本地闸门的 `e2e-record` 域运行它。长文本、多条目等压力变体写在场景的 `api` 字段里。
- **页面截图**：场景登记 `screenshot` 后，设置 `E2E_SCREENSHOTS=1` 运行才会比对，只在 1024×600、1440×900、2560×1440 三个视口下截图。CI 的 `frontend-e2e` 设置了这个变量，截图出现差异或缺少基线时 job 失败。基线只能在容器里生成和比对：用 `pnpm e2e:server` 与 `pnpm e2e:remote` 运行，新增或有意改动的截图加 `--update-snapshots=changed` 重新生成，逐张核对后提交。本机渲染的字体与容器不同，不要用它生成基线。

## 代码质量

代码规范的入口是 [`CODING_STANDARDS.md`](https://github.com/ArcReel/ArcReel/blob/main/CODING_STANDARDS.md)：按改动路径索引到 `docs/standards/` 下的领域规范，只收工具替代不了、需要判断的项目约定。本地审查和 PR 上的 AI reviewer 都以它为依据。

### 依赖管理

新增或升级依赖的步骤见 [`docs/agents/dependencies.md`](https://github.com/ArcReel/ArcReel/blob/main/docs/agents/dependencies.md)。

## 文档维护

[文档站](https://docs.arc-reel.com/)发布 `website/docs/` 的中文源文和 `website/i18n/en/` 的英文译文。根目录的 `CONTRIBUTING.md` 会在本地预览和构建时自动复制到文档站。修改文档站页面、README、贡献指南或站点界面文案时，请在同一个 PR 中同步更新中英文。翻译核对与校验流程见 [AGENTS.md](https://github.com/ArcReel/ArcReel/blob/main/AGENTS.md) 的文档维护指引。

### 各页职责

- **README** 介绍 ArcReel 的用途，展示可公开的案例，并给出最短上手路径。
- **用户指南** 说明创作者如何使用 ArcReel。入门教程负责首次使用路径，各主题指南负责完整操作和限制。
- **运维文档** 说明部署、升级、备份恢复和数据库迁移。
- **开发文档** 帮贡献者建立本地开发环境、理解架构和找到扩展入口。仓库内的标准、术语表和 ADR 提供进一步的工程约束和决策背景。

### 写作约定

改动功能时，请在同一个 PR 中同步更新对应文档。写作规范见[用户文档规范](https://github.com/ArcReel/ArcReel/blob/main/docs/standards/docs.md)和[通用代码规范](https://github.com/ArcReel/ArcReel/blob/main/docs/standards/general.md)。上站页面的 Markdown 标题统一写作 `## 标题 {#english-id}`，以便中英文共用锚点。

发布截图、视频和示例前，请先清除凭证、内部地址、个人信息和私有项目内容，并确认所用的小说、品牌、音乐等素材均已获得发布授权。示例应展示 ArcReel 的实际产出；如需列出费用，应同时注明币种与适用案例。

## 工作流程

### 分支策略（trunk-based）

- 只有 `main` 是长期分支。所有工作从最新 `main` 切短分支完成，PR 合回 `main`
- 禁止直接 push 到 `main`。个人分支同样经 PR 流程合并，提交前自行检查 diff 与验收清单

### 分支命名约定

采用 `<type>/<slug>` 格式，其中 `type` 取自 conventional commit 类型之一：

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

分支从创建到合并不超过 3 天。超期应拆分，或先 rebase 到主线同步，避免将长期分支直接提交 review。

### Squash merge

每个 PR 压缩为 1 个 commit 合并回 `main`，commit message 遵循 conventional commits 规范（见下节）。在 GitHub 上选择 "Squash and merge"。

`afk-team-workflow` 生成的 stage PR 例外：采用 "Rebase and merge" 以保留每个 issue 各自的 conventional commit；集成审查、验收及 review loop 中的修复统一合入 issue commits 之后的同一个 conventional integration-fix commit，并以 `--force-with-lease` 推送。

## 提交规范

提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范。

```
feat: 新增功能描述
fix: 修复问题描述
refactor: 重构描述
docs: 文档变更
chore: 构建/工具变更
```

标题格式为 `type(scope): 摘要`，其中 scope 可省略。采用 Squash merge 时，PR 标题会直接作为 changelog 条目：请描述用户可感知的收益，范围词使用产品术语而非实现术语（如 status_code、内部类名等），并如实限定范围。type 取值与 changelog 分类见下文「发版流程」与 `.release-please-config.json`。

## 发版流程

版本号与 changelog 由 [release-please](https://github.com/googleapis/release-please) 自动维护（配置见 `.release-please-config.json`，workflow 见 `.github/workflows/release-please.yml`）。**开发者无需手动 bump 版本号**，只需编写合规的 conventional commits 即可。

### 工作流程

1. 普通 PR 按 conventional commits 规范以 squash merge 合入 `main`；`afk-team-workflow` 的 stage PR 按上述例外以 rebase merge 合入
2. release-please 扫描自上次发版以来的 commit，自动创建或更新标题形如 `chore(main): release X.Y.Z` 的 Release PR，其中包含下一个版本号以及更新后的 `CHANGELOG.md`
3. 合并该 Release PR 后，系统会自动创建 `vX.Y.Z` tag 并发布对应的 GitHub Release

### commit type → 版本步进

| commit type | 版本步进 | changelog |
|-------------|---------|-----------|
| `feat`      | minor   | ✨ 新功能 |
| `fix`       | patch   | 🐛 Bug 修复 |
| `feat!` / 任意 type + `!` / footer 含 `BREAKING CHANGE:` | **major**（版本 <1.0.0 时为 minor） | ⚠️ BREAKING CHANGES（changelog 置顶） |
| `chore` / `ci` / `build` / `test` / `style` | 不步进 | 隐藏 |

`perf`、`refactor`、`docs` 和 `revert` 也会列入 changelog。

`pyproject.toml` 和 `frontend/package.json` 中的 `version` 字段由 release-please 自动维护（见 `pyproject.toml` 中的 `# managed by release-please` 注释），**开发者应将其视为只读**。`uv.lock` 同样由 release-please workflow 在 Release PR 分支上自动运行 `uv lock` 同步。出厂模型目录快照（`lib/catalog/snapshot/`）也会在 Release PR 分支上自动更新：workflow 会拉取目录数据仓库的最新数据，通过校验后自动提交；若拉取失败或校验未通过，快照保持不变，同时 workflow 会给出提示。实际版本状态以 git tag 和 `.release-please-manifest.json` 为准。

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

**本仓库不使用破坏性变更标记。** 前后端同仓一体发布，后端 API 不做版本化对外承诺——自带前端随版本同步演进，外部集成通过 `/agent-installation-guide.md` 获取当前安装入口，不依赖版本号。接口删改按 `fix`/`refactor` 正常分类，不加 `!` 后缀、不写 `BREAKING CHANGE:` footer。若误标且已合并，则按 merge 方式分别纠正：普通 squash PR 编辑正文追加 `BEGIN_COMMIT_OVERRIDE`/`END_COMMIT_OVERRIDE` 块，等待下一次 main push 或手动重跑 workflow；AFK rebase stage 则在最后一次 main push 更新 Release PR 后，直接校正其版本与 changelog 产物并通过完整性校验，再合并 Release PR。0.x 阶段的 `bump-minor-pre-major` 仅把误标的版本跃迁限制为 minor，不修正 changelog。

以下语法说明仅用于识别误标。**破坏性变更**有两种等价写法：

```
# 写法 1：type 后加 !
feat(api)!: 移除 /api/v1/legacy 端点

# 写法 2：footer 含 BREAKING CHANGE（更常用，可以写多行说明）
feat(auth): 统一 API Key 验证逻辑

BREAKING CHANGE: /api/v1/api-keys 的返回结构改为 { items: [...] }，
旧客户端需要适配。
```

对于以上两种写法，release-please 都会：
- 将版本号 bump 为 major；在当前版本 <1.0.0 时受 `bump-minor-pre-major` 配置约束，仅 bump minor
- 在 changelog 顶部插入独立的 **⚠️ BREAKING CHANGES** 区块，汇总展示每条破坏性变更的说明
- 在对应 type 分类（如 `✨ 新功能`）下保留该 commit 的常规条目
