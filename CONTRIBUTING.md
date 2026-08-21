# 贡献指南

欢迎贡献代码、报告 Bug 或提出功能建议。

## 本地开发环境

```bash
# 前置要求：Python 3.12+, Node.js 20+, uv, pnpm, ffmpeg
# 文档站 website/ 另需 Node 24（版本固定于 website/.node-version）
# 操作系统：Linux / macOS / Windows WSL2；Windows 原生可运行项目创建与基础流程，
# Agent 沙箱在 Windows 上降级为命令前缀白名单（见 docs/adr/0025），生产部署推荐 WSL2/Docker

# 安装依赖
uv sync
cd frontend && pnpm install && cd ..

# 一次性安装 pre-commit 钩子（ruff / eslint / pull_request_target tripwire）
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
pnpm check        # typecheck + lint + format:check，与 CI 的三项静态检查等价

# 站内搜索仅在构建产物上可用，dev server 中不可用
pnpm build && pnpm serve

# 将仓库根 CONTRIBUTING.md 同步为开发区页面（start / build 已自动前置执行，通常无需手动运行）
pnpm sync-contributing

# CI 一致性检查：页面清单 / 孤立译文 / 上站文档标题缺少显式锚点 / UI JSON key 完整性，任一不满足即非零退出；
# 依赖 sync-contributing 的产物，须先运行 sync-contributing
pnpm check-consistency
```

## 测试

```bash
# 后端测试；单文件：uv run python -m pytest path/to/test.py，-k 关键字筛选，-v 详细输出
uv run python -m pytest

# 前端 typecheck + lint + 测试
cd frontend && pnpm check
```

pytest `asyncio_mode = "auto"`，异步用例无需手动标记。

> **过渡期说明**：存量测试正按整改 Spec 分批向本章对齐。下文标注「闸门」的规则由 `scripts/audit_tests.py --check` 机械强制，每道闸门与对应存量清零同一 PR 上线；目录迁移与 marker 自动注入落地前，分类 marker 仍需手写（收集期强制恰好一个，语义按下表）。整改完成后删除本段。

### 分层与目录

后端每个用例恰好属于一个档位，CI 默认执行 `-m "not e2e"`：

| 档位 | 语义 | 边界 |
|---|---|---|
| `unit` | 快速、隔离 | 禁真实 DB、子进程、网络；允许 `tmp_path` 本地文件系统 |
| `integration` | 跨模块真实协作 | 真实 DB、文件系统、ffmpeg 子进程；触真实 DB（含全部 alembic 迁移测试）一律归此档 |
| `e2e` | 端到端 | 依赖真实外部服务（远程 API、大模型调用）；CI 默认跳过，本地按需运行 |

- 目录为 `tests/unit|integration|e2e/<源码顶层包镜像>`（如 `tests/unit/lib/…`、`tests/integration/server/…`）。档位 marker 由 conftest 按路径自动注入，不手写；`uses_db` 与 `unit` 同现在收集期报错。镜像的正确性靠 review，不设机械校验。
- alembic 迁移测试保持每个迁移脚本一个文件，位于 `tests/integration/lib/db/migrations/`，共享该目录 conftest 的 `alembic_cfg`；迁移测试维持 SQLite，PostgreSQL 侧由 CI workflow 的 alembic 升降级命令兜底。

### 体量与命名

- 一个测试文件对应一个被测对象；同一被测对象超出可读体量时按行为域拆分，用语义化主题后缀命名子文件。
- 闸门两条：禁 `_more` / `_full` / `_coverage` / `_extra` / `_additional` 分裂后缀；单文件 3000 行熔断（语义是熔断无界膨胀，不是行数标准）。

### 测试替身

- **优先级**：真实对象（内存 SQLite、`tmp_path`）＞ `tests/fakes.py` 手写替身（收录边界见其模块 docstring）＞ 带 `spec`/`autospec` 的 Mock ＞ 裸 `MagicMock`/`AsyncMock`。Mock 只替换仓库边界（第三方 SDK、网络传输、子进程、文件系统、时钟）；仓库内协作者用真实对象或手写 fake。
- **禁止 patch 生产代码私有符号**（闸门，无豁免）：`patch("lib.x._y")`、`monkeypatch.setattr(mod, "_y")`、`patch.object(Cls, "_y")` 三种形式一律禁止。需要控制内部行为时走 seam。
- **seam 即显式参数注入**：构造参数或关键字参数，带生产默认值，不改变生产行为，如 `retry_async(operation, *, clock=..., jitter=...)`；不引入模块级可替换全局。适用面：轮询时钟/间隔/退避、能力解析器、HTTP 探测客户端、文件系统与子进程。
- **出站 HTTP 断言用 respx**：保留真实 httpx 客户端，在 transport 层拦截（`AsyncOpenAI` 流量同样被捕获），断言真实序列化后的请求。
- **patch 收编**（闸门）：同一 patch 目标字符串出现在 ≥3 个测试文件时收编为共享 fixture / helper，各文件不再直写；FastAPI 路由依赖优先 `app.dependency_overrides` 而非 patch。

### 无意义测试判据

无意义测试是负价值，删除优于保留。三条机械判据（闸门）：

1. **断言全部落在替身调用记录上**——测试对象成了 mock 本身。改为断言真实产出：返回对象的类型与属性、respx 捕获的真实请求、可观察状态。
2. **patch 被测单元自身的逻辑步骤后再测该单元**——integration 用例 mock 被测 module 的公共入口是其特例。
3. **零断言用例**——说得出想保护什么就补断言，说不出就删。

命中后处置固定三步，不逐条自由裁量：① 行为已有其他用例实质覆盖 → 删，不做覆盖补偿；② 无覆盖但行为不值得保护（无真实分支或契约约束）→ 删；③ 值得保护 → 改造，需要生产代码加 seam 的转入 seam 收编批次。

四条审计标准靠 review 与专项审计判断，不落闸门：重复弱化（与他例同路径且断言更弱）、过度表征（断言日志文本、字典键序、私有属性等实现细节而非契约）、setup 与断言严重失衡、断言辅助函数内部只断替身调用记录。

### 共享设施

- **三角色**：`tests/conftest.py` 只放 fixture 与收集期钩子，禁止被 import（闸门）；`tests/fakes.py` 放替身实现，不含 fixture；`tests/factories.py` 放测试输入构造器（数据与媒体文件 builder）。专题共享模块（如 `tests/auth_deps.py`）允许存在；fakes / factories / 专题模块的公开符号须被 ≥2 个测试文件使用（闸门），仅单文件使用的搬回该文件。
- **局部 conftest**：只为本目录提供 fixture；不得与根 conftest 的 fixture 同名；跨目录共用的上提根 conftest；conftest 之间不互相 import（闸门）。
- **fixture 覆写与重复**（闸门）：测试文件不得定义与任一 conftest 同名的 fixture；同名 fixture 在 ≥3 个测试文件重复定义时上提 conftest。
- **DB fixture**：一律派生自唯一的方言感知 engine fixture，由此自动获得 `uses_db` 标记与 PostgreSQL 兼容选集。

### 时序与偶发失败

- 等待、重试、超时逻辑一律经时钟 seam 或事件握手驱动，不用真实 `time.sleep` 等墙钟等待。
- 偶发失败（flaky）视同普通缺陷：就地修（时钟 seam / 事件握手），修不动或不值得修则按无意义测试判据删。不引入自动重试（pytest-rerunfailures、CI job 级 retry）——自动重试把可见信号变成不可见的税。
- 概率性 stress 用例（真实并发 + 真实时间）须在此显式在册。当前唯一在册豁免：`tests/test_project_manager_concurrent_save.py` 的原子写压力用例（`integration` 档）。

### 覆盖率

覆盖率是信号，不是闸门：CI 不因覆盖率数字失败，Codecov 为唯一信号载体（PR 覆盖评论与趋势图，status 一律 informational）。不为覆盖率数字写测试；删除无意义测试允许覆盖率下降。

### 闸门

- 入口唯一：`uv run python scripts/audit_tests.py --check`，本地与 CI（独立 `test-lint` 步骤）同一条命令；输出 `规则号 file:line 修复指引`。
- 零容忍：违规数恒为 0，无基线、无棘轮、无豁免标注；脚本误报的处置是修脚本，不是给用例贴例外；新增规则与其存量清零同 PR 上线。
- 分工：AST 脚本管代码结构；pytest 收集期只做档位相关校验；运行期不新增检查。前端语义类规则归 eslint，结构类规则（命名禁令、3000 行熔断）由同一脚本扫 `frontend/src/**/*.test.*`。

### 前端测试（vitest）

- **API 打桩**：`vi.spyOn(API, method)` 是正统边界（`API` class 是前端唯一出站口）；`api.ts` 本体测试用手写 fetch/Response stub；不引入 msw。禁整模块 `vi.mock("@/api")` 与 `vi.mock("react-i18next")`（eslint 强制；全局 setup 已加载真实中文 i18n，mock 掉会断不出翻译缺失）。
- **SSE 打桩**：统一用 `src/test/` 的共享 `FakeEventSource`，spy `API.openProjectEventStream` 返回它。
- **mock 内部子组件**须属三类之一：重量级（虚拟化/动画/canvas）、有副作用（发请求/起 timer）、与本测试无关的纯展示；同一组件被 ≥3 个文件 mock 时上提 `src/__mocks__/`。
- **共享设施**：与被测对象无关的横切工具（`createDeferred`、`FakeEventSource`、factories）重复出现在 ≥3 个文件时上提 `src/test/`；API spy 组合豁免收编（各文件组合互不相同，收编只会造出上帝 helper）；本地 `renderXxx` 仅同形重复 ≥3 才上提。
- **目录与体量**：测试文件与源文件同级并放，不用 `__tests__/` 目录；「一文件一被测对象」、分裂命名禁令与 3000 行熔断与后端相同，允许语义化主题后缀（如 `ShotDetail.drama.test.tsx`）。
- **可测性改造**：禁生产行为改动；允许抽纯函数、抽 hook 级的结构性抽取。
- **配置与 lint**：`testTimeout` 用 vitest 默认 5s，个别慢用例显式覆写并说明；eslint 启用 vitest、testing-library、jest-dom 插件（`expect-expect` 拦零断言）；裸 `toHaveBeenCalled` 不设禁令，断言强度归 review。

## 代码质量

**Lint & Format（ruff）：**

```bash
uv run ruff check . && uv run ruff format .
```

- 规则集：`E`/`F`/`I`/`UP`，忽略 `E402` 和 `E501`
- line-length：120
- CI 中强制检查：`ruff check . && ruff format --check .`

**类型检查（basedpyright）：**

```bash
uv run basedpyright
```

- standard 模式 + `reportMissingTypeStubs = false`，CI 强制 0 error，pre-push hook 执行全量扫描
- tests/ 内 `reportOptional*` 和 `unknown*` 系列降级为 warning，避免大量使用 mock 的测试产生噪声
- 第三方 untyped 库通过行级 `# pyright: ignore[...]` 处理

**Import 分层契约（import-linter）：**

```bash
uv run lint-imports
```

- 校验 `lib.config < lib.*_backends < lib.custom_provider` 分层契约，是 CI backend-static 的必过步骤
- 新增 ignore 条目前先确认该依赖边无法直接消除（约定见 `pyproject.toml`）

**Lint（前端 ESLint）：**

```bash
cd frontend && pnpm lint          # 检查
cd frontend && pnpm lint:fix      # 自动修复可修复的问题
```

- 配置：`frontend/eslint.config.js`（flat config）
- 规则集：`typescript-eslint/recommendedTypeChecked` + `react/recommended` + `react-hooks/recommended` + `jsx-a11y/recommended`
- typed linting 启用 `projectService: true`，可检查 `no-floating-promises`、`no-misused-promises` 等 async 相关问题
- CI 中强制检查：`frontend-tests` job 的 `Lint` step

**Lint & Format（文档站 ESLint + prettier）：**

```bash
cd website && pnpm check          # typecheck + lint + format:check
cd website && pnpm lint:fix       # ESLint 自动修复可修复的问题
cd website && pnpm format         # prettier 写入
```

- 配置：`website/eslint.config.mjs` + `website/.prettierrc.json`（`website/` 是独立包根，工具链与 `frontend/` 各自独立，因为两者的 TypeScript 大版本不同）
- ESLint 规则集与 frontend 相同：`typescript-eslint/recommendedTypeChecked` + `react/recommended` + `react-hooks/recommended` + `jsx-a11y/recommended`
- prettier printWidth 120（与后端 ruff 的 line-length 对齐）；`docs/` 与 `i18n/` 不参与格式化，排除依据见 `website/.prettierignore` 顶部注释
- CI 中强制检查：`website-checks` job 的 `Typecheck` / `Lint` / `Format check` 三个 step，均排在 `Build` 之前

### 依赖管理

前后端新增/升级依赖一律用 `uv add` / `pnpm add`，不手动写入版本号到 `pyproject.toml` / `package.json`；新增依赖后同步 `.github/dependabot.yml` 的 patterns 归入对应分组。

### 注释规范

代码与测试注释仅描述当前行为与约束，不写 issue/PR/Spec 编号，也不使用时间性措辞（「最近」「本次」「实测」）；此类信息写在 commit message / PR 描述中。修改文件时一并清除已有的此类引用。`docs/` 下专门文档之间互引 spec 不受此限。

### ESLint disable 使用规范

前端 ESLint 采用零 warning 政策，所有规则均为 error。如须绕过，遵循以下约定：

- **形式**：`// eslint-disable-next-line <rule> -- <中文理由>`，`--` 后的理由**强制**
- **禁用**：文件级 `/* eslint-disable */`、无理由的 `// eslint-disable-line`、`@ts-ignore` 联用
- **PR 描述要求**：新增的 disable 必须在 PR body 以表格列出 `rule | file:line | 理由`
- **文件级关闭**只允许通过 `eslint.config.js` 的 `files` override，且须在 config 注释说明原因
- **不可接受的理由**：「太麻烦」「暂时这样」「later fix」
- **可接受的理由示例**：「React setter 引用稳定」「mount-only 初始化」「生成式预览视频无字幕源」

**本地 IDE 建议（不提交到仓库）：**

`.vscode/` 已在 `.gitignore`。自行添加 `frontend/.vscode/settings.json` 可让 VS Code / Cursor 实时显示 lint 提示并在保存时自动修复：

```json
{
  "eslint.workingDirectories": [{ "pattern": "./frontend" }],
  "editor.codeActionsOnSave": { "source.fixAll.eslint": "explicit" }
}
```

**已知约束：**

- TypeScript 版本锁：`typescript-eslint` 的 peer 范围限制 TypeScript 上限；升级 TypeScript 前先核对锁定版本的 peer 范围，必要时同步升级 `typescript-eslint`

## 文档维护

用户文档的唯一发布位置是 [docs.arc-reel.com](https://docs.arc-reel.com)，源文件在 `website/docs/`（本地构建与预览见上文「文档站」）。中文是唯一写作源，英文译文由 AI 生成，人工仅审校中文源。内部文档（ADR、`CONTEXT.md`、`AGENTS.md`、安全威胁模型、供应商 API 文档索引等）不上站，留在仓库 `docs/` 下；`SECURITY.md` 因 GitHub Security 选项卡依赖也留在仓库根。

本文件是贡献指南的真相源，构建时复制为站点的开发区页面（`website/scripts/sync-contributing.mjs`），中文副本不入库。

### 各页职责

| 页面 | 应该包含 | 不应该包含 |
|---|---|---|
| `README.md` | 产品定位、核心价值、最短上手路径 | 完整模型清单、所有环境变量、内部实现细节 |
| `website/docs/index.mdx` | 文档站定位、主要入口和导航概览 | 具体功能的完整操作步骤 |
| `website/docs/guide/getting-started.md` | 从部署到第一条成片的完整操作路径 | 生产级反向代理和备份策略 |
| `website/docs/guide/workflows.md` | 内容模式、视频生成路线、审核节点、选择建议 | 供应商密钥和运维命令 |
| `website/docs/guide/providers.md` | 供应商类型、覆盖能力、选择原则、配置层级 | 容易过期的价格承诺 |
| `website/docs/guide/jianying-export.md` | 剪映草稿目录定位、导出与二次编辑操作步骤 | 视频生成本身的流程说明 |
| `website/docs/guide/faq.md` | 高频问题和短答案 | 长篇教程 |
| `website/docs/ops/deployment.md` | 部署、升级、备份、恢复、监控和安全 | 产品营销文案 |
| `website/docs/ops/migrate-to-postgres.md` | SQLite 到 PostgreSQL 的迁移步骤、校验和回滚 | PostgreSQL 的日常部署与运维手册 |
| `website/docs/dev/architecture.md` | 稳定的架构边界、数据流和扩展点 | 临时实现计划和未完成设计 |
| `SECURITY.md` | 支持版本、支持的部署边界、私密漏洞报告和协调披露政策 | 未修复漏洞细节和动态风险登记 |
| `docs/security/threat-model.md` | 安全资产、信任边界、攻击面、现有控制和重评触发条件 | 可直接利用的未修复漏洞与补丁历史 |

### 写作约定

- **README 保持稳定**：README 只需让第一次访问仓库的人回答「ArcReel 是什么、适不适合我、和直接调用模型 API 有什么区别、如何最快运行起来」。具体模型名称、单价和接口参数放到站点对应页面，避免供应商每次更新都要重写首页。
- **供应商信息以运行时能力为准**：文档描述覆盖哪些媒体类型、ArcReel 如何统一配置、不同能力如何选择、具体信息在哪里确认；设置页中实际可选的模型与供应商官方文档是最终依据。
- **标题带显式锚点 ID**：上站页面的每个标题写成 `## 标题 {#english-id}`，中英两个 locale 共用同一锚点，避免中文自动 slug 随文案改动而失效。站内互引用相对文件路径（如 `../ops/deployment.md`），指向未上站的仓库文件时用 GitHub 绝对链接。
- **文档变更应与功能变更一起提交**：新增内容模式或视频生成路线、新增供应商或媒体能力、部署目录/端口/环境变量变化、数据目录/备份方式/迁移行为变化、对外 API/许可证或商业使用方式变化，均须同步更新对应文档。
- **上站 `.md` 不能使用 JSX / import**：`website/docusaurus.config.ts` 设 `markdown.format: "detect"`，`.md` 按 CommonMark 解析而非 MDX：两者都不会报编译错误，但也都不会按 MDX 语法执行——JSX 标签被当作原始 HTML 原样输出（带子内容的标签，子内容会直接显示成页面文本），import 语句被当作普通文本原样显示。需要 JSX 的页面改用 `.mdx`。

## 工作流程

### 分支策略（trunk-based）

- 只有 `main` 是长期分支。所有工作从最新 `main` 切短分支完成，PR 合回 `main`
- 禁止直接 push 到 `main`。个人分支同样经 PR 流程合并，提交前自行检查 diff 与验收清单

### 分支命名约定

`<type>/<slug>`，`type` 取 conventional commit 类型之一：

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

1. PR 按 conventional commits 规范 squash merge 到 `main`
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

**本仓库不使用破坏性变更标记。** 前后端同仓一体发布，后端 API 不做版本化对外承诺——自带前端随版本同步演进，外部集成（OpenClaw 等）经 `/skill.md` 运行时拉取最新契约、不依赖版本号，删改 `public/skill.md.template` 引用的端点时同步更新该模板。接口删改按 `fix`/`refactor` 正常分类，不加 `!` 后缀、不写 `BREAKING CHANGE:` footer。误标合并后的纠正方式：编辑该 PR 正文追加 `BEGIN_COMMIT_OVERRIDE`/`END_COMMIT_OVERRIDE` 块，release-please 按 override 重算 changelog 与版本号（需 squash 合并，本仓库满足）；workflow 仅在 main push 时运行，编辑后需等下一次 main push 或手动重新运行 release-please workflow 才生效。0.x 阶段的 `bump-minor-pre-major` 仅把误标的版本跃迁限制为 minor，不修正 changelog。

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
