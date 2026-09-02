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
# 后端完整测试；单文件可直接替换 tests/ 路径，-k 仅用于人工按名称筛选
uv run python -m pytest -n 4 --dist loadfile

# 前端 typecheck + lint + 测试
cd frontend && pnpm check
```

pytest `asyncio_mode = "auto"`，异步用例无需手动标记。

### 测试选择

开发循环先跑与改动相关的最小测试集，任务完成和 push 前再跑受影响域的完整闸门。选择结果为 0 个测试时扩大到对应目录或完整测试集，不把 0 个测试视为验证通过。

- 后端测试文件变更：直接运行这些文件；源码变更：优先运行 `tests/unit|integration/` 下镜像路径及已知消费方。路径能缩小收集范围，`unit` / `integration` / `uses_db` marker 只用于跨路径筛选，`-k` 只用于人工定位。
- 后端完整测试：改动 `pyproject.toml`、`uv.lock`、根 `tests/conftest.py`、含行为的包初始化、测试选择规则，或涉及 Alembic、profile、DB、i18n、共享测试设施时执行。无法可靠判断影响范围时也执行完整测试。
- 前端测试文件变更：直接传文件给 Vitest；普通源码变更：在 `frontend/` 运行 `pnpm exec vitest related --run <source files>`；分支级检查运行 `pnpm exec vitest run --changed <base>`。TypeScript 源码变更同时运行完整 typecheck。
- 前端完整测试：改动 `package.json`、`pnpm-lock.yaml`、`vitest.config.*`、测试 setup、i18n 或 branding 时执行 `pnpm check`。相关测试选择为 0 时先扩大到所在功能目录，仍无法确定时执行 `pnpm check`。
- 任一测试文件变更后运行 `uv run python scripts/audit_tests.py --check`。

### 分层与目录

后端每个用例恰好属于一个档位，CI 默认执行 `-m "not e2e"`：

| 档位 | 语义 | 边界 |
|---|---|---|
| `unit` | 快速、隔离 | 禁真实 DB、子进程、网络；允许 `tmp_path` 本地文件系统 |
| `integration` | 跨模块真实协作 | 真实 DB、文件系统、ffmpeg 子进程；使用真实 DB 的用例（含全部 alembic 迁移测试）一律归此档 |
| `e2e` | 端到端 | 依赖真实外部服务（远程 API、大模型调用）；CI 默认跳过，本地按需运行 |

- 目录为 `tests/unit|integration|e2e/<源码顶层包镜像>`（如 `tests/unit/lib/…`、`tests/integration/server/…`）。档位 marker 由 conftest 按路径自动注入，无需手写；同时命中 `uses_db` 与 `unit` 的用例在收集期报错。镜像的正确性靠 review，不设机械校验。
- alembic 迁移测试保持每个迁移脚本一个文件，位于 `tests/integration/lib/db/migrations/`，共享该目录 conftest 的 `alembic_cfg`；迁移测试维持 SQLite，PostgreSQL 侧由 CI workflow 的 alembic 升降级命令兜底。

### 体量与命名

- 一个测试文件对应一个被测对象；同一被测对象超出可读体量时按行为域拆分，用语义化主题后缀命名子文件。
- 闸门两条：禁 `_more` / `_full` / `_coverage` / `_extra` / `_additional` 分裂后缀；单文件 3000 行熔断（用于阻止文件无界膨胀，不作为体量标准）。

### 测试替身

- **优先级**：真实对象（内存 SQLite、`tmp_path`）＞ `tests/fakes.py` 手写替身（收录边界见其模块 docstring）＞ 带 `spec`/`autospec` 的 Mock ＞ 裸 `MagicMock`/`AsyncMock`。Mock 只替换仓库边界（第三方 SDK、网络传输、子进程、文件系统、时钟）；仓库内协作者用真实对象或手写 fake。
- **禁止 patch 生产代码私有符号**（闸门，无豁免）：`patch("lib.x._y")`、`monkeypatch.setattr(mod, "_y")`、`patch.object(Cls, "_y")` 三种形式一律禁止。需要控制内部行为时走 seam。
- **seam 即显式参数注入**：构造参数或关键字参数，带生产默认值，不改变生产行为，如 `retry_async(operation, *, clock=..., jitter=...)`；不引入模块级可替换全局。适用范围：轮询时钟/间隔/退避、能力解析器、HTTP 探测客户端、文件系统与子进程。
- **进程级缓存的重置钩子取公开名**：生产模块用 `functools.cache` 之类的进程级缓存时，为测试暴露的重置入口写成公开的 `reset_*_for_tests()`（如 `lib.app_data_dir.reset_for_tests`），不写下划线私有名——测试 import 私有符号既撞上上一条禁令，也会被 basedpyright 的 `reportUnusedFunction` 判成死代码。钩子只清缓存、不改生产行为。它是过渡形态，新代码优先按上一条做参数注入。
- **出站 HTTP 断言用 respx**：保留真实 httpx 客户端，在 transport 层拦截（`AsyncOpenAI` 流量同样被捕获），断言真实序列化后的请求。
- **patch 收编**（闸门）：同一 patch 目标字符串出现在 ≥3 个测试文件时收编为共享 fixture / helper，各文件不再各自定义；FastAPI 路由依赖优先 `app.dependency_overrides` 而非 patch。

### 无意义测试判据

无意义测试是负价值，删除优于保留。四条机械判据（闸门）：

1. **断言全部落在替身调用记录上**——测试对象变成 mock 本身。改为断言真实产出：返回对象的类型与属性、respx 捕获的真实请求、可观察状态。
2. **patch 被测单元自身的逻辑步骤后再测该单元**——integration 用例 mock 被测 module 的公共入口是其特例。
3. **零断言用例**——能明确要保护的行为则补上断言，否则删除。
4. **同文件内函数体等同的用例**——两条用例去掉 docstring 后函数体的规范化表示相同（`ast.unparse` 抹平换行、括号与引号风格，不是源码逐字比对），或一条的函数体等同于同文件某个 `@parametrize` 用例参数表中的一行。判定限定同文件（跨文件同名局部辅助函数会造成整批误报），并要求形参名（含默认值）、非参数化装饰器与类级上下文——类装饰器、基类，以及类体内一切非测试成员（`setup_method`、类级 fixture、`pytestmark`）——全部相同；只扫后端 `tests/`，前端同类判断归 review。

命中后处置固定三步，不逐条自由裁量：① 行为已有其他用例实质覆盖 → 删除，不做覆盖补偿；② 无覆盖但行为不值得保护（无真实分支或契约约束）→ 删除；③ 值得保护 → 改造，需要在生产代码中加 seam 的转入 seam 收编批次。

四条审计标准靠 review 与专项审计判断，不落闸门：重复弱化（与其他用例覆盖同一路径且断言更弱；其中「函数体等同」这一子形态已收进判据 4，此处只剩断言为真子集之类须人判的形态）、过度表征（断言日志文本、字典键序、私有属性等实现细节而非契约）、setup 与断言严重失衡、断言辅助函数内部只断言替身调用记录。

### 共享设施

- **三角色**：`tests/conftest.py` 只放 fixture 与收集期钩子，禁止被 import（闸门）；`tests/fakes.py` 放替身实现，不含 fixture；`tests/factories.py` 放测试输入构造器（数据与媒体文件 builder）。专题共享模块（如 `tests/auth_deps.py`）允许存在；fakes / factories / 专题模块的公开符号须被 ≥2 个测试文件使用（闸门），仅单个文件使用的移回该文件。
- **局部 conftest**：只为本目录提供 fixture；不得与根 conftest 的 fixture 同名；跨目录共用的 fixture 上提到根 conftest；conftest 之间不互相 import（闸门）。
- **fixture 覆写与重复**（闸门）：测试文件不得定义与任一 conftest 同名的 fixture——供给同一实体的改为直接消费 conftest 版本，供给不同实体的改一个有区分度的名字；同一实体的 fixture 在 ≥3 个测试文件重复定义时上提 conftest。
- **DB fixture**：一律派生自 `tests/conftest.py` 唯一的 engine 构造点 `make_test_engine`。`session_factory` / `async_session` 方言感知，`DATABASE_URL` 指向 PostgreSQL 时走真实 PG；`concurrent_session_factory` 同样方言感知，并为 SQLite 提供允许独立连接的 WAL 文件库；`file_session_factory` 恒为文件 SQLite，消费方是标了 `sqlite_only` 的边界用例。四者携带 `uses_db` 标记，构成需要数据库的选择集；PostgreSQL 兼容 job 取其中 `uses_db and not sqlite_only` 的部分，`file_session_factory` 的消费方不在其内。`db_engine` / `db_session` / `db_factory`（内存）与 `file_db_factory`（文件）固定走 SQLite、不带 `uses_db`，供不进该选集的 models 与 repositories 单测使用。唯一登记的例外是 `async_session` 的 PG 分支：它绑定 CI job 已 `alembic upgrade head` 建好的 public schema，隔离原语是外层事务 + SAVEPOINT，与 `make_test_engine` 的 per-test schema + `create_all` 不同，故自建 engine。

### 时序与偶发失败

- 等待、重试、超时逻辑一律经时钟 seam 或事件握手驱动，不使用 `time.sleep` 之类的真实时间等待。
- 偶发失败（flaky）视同普通缺陷：就地修复（时钟 seam / 事件握手），无法修复或不值得修复的按无意义测试判据删除。不引入自动重试（pytest-rerunfailures、CI job 级 retry）——自动重试会掩盖本应暴露的失败。
- 概率性 stress 用例（真实并发 + 真实时间）须在本节显式登记。当前唯一登记的豁免：`tests/integration/lib/test_project_manager_concurrent_save.py` 的原子写压力用例。

### 覆盖率

覆盖率是信号，不是闸门：CI 不因覆盖率数字失败，Codecov 为唯一信号载体（PR 覆盖评论与趋势图，status 一律 informational）。不为覆盖率数字写测试；删除无意义测试允许覆盖率下降。

### 变异测试

变异得分是信号，不是闸门，与覆盖率同构：不为杀死 mutant 写测试，不做覆盖补偿，不进 CI。按批次手动跑，把本批模块填进 `pyproject.toml` 的 `[tool.mutmut] only_mutate`；操作步骤与已知陷阱见 [`docs/testing/mutmut-runbook.md`](docs/testing/mutmut-runbook.md)。

- **存活只是把测试送上审查台，不是删除依据**。存活 mutant 落在已有测试覆盖的代码上时，按无意义测试判据的三步处置逐条判定；两种情形不处置：保持既有用例输入不变只加强断言杀不死、须换输入才能杀死的（属覆盖补偿，不做），以及等价变异体（改了源码但行为不变）。无覆盖代码上的存活一律不管。
- **超时不算 killed**。mutmut 的 fork 模型与本仓库的多线程测试套件不相容，真正存活的 mutant 会被记成超时；`.meta` 里 exit code 不等于 1 的一律在新进程里只跑关联用例复核，复核确认存活的才进入判定。
- **改造后的三层验收**（`scripts/mutmut_compare.py`）：本次改造针对的 mutant 全部 killed；基线里 killed 的 mutant 没有一个变成存活，变成超时的须新进程复核；基线里存活且本次未改造的 mutant 被杀死只登记不报警，其中判为等价变异体的被杀死即整轮作废。改造 PR 的 diff 不得触及 `lib/` 与 `server/`。

### 闸门

- 入口唯一：本地 `uv run python scripts/audit_tests.py --check`，CI 的独立 `test-lint` 作业跑同一脚本、同一 `--check`（脚本零第三方依赖，该作业只装 Python 不装项目依赖）；输出 `规则号 file:line 修复指引`。
- 零容忍：违规数恒为 0，无基线、无棘轮、无豁免标注；脚本误报通过修改脚本解决，不为用例添加豁免；新增规则与其存量清零同 PR 上线。
- 分工：AST 脚本负责代码结构；pytest 收集期只做档位相关校验；运行期不新增检查。前端语义类规则归 eslint，结构类规则由同一脚本扫描 `frontend/src/**/*.test.*`。

### 前端测试（vitest）

- **API 打桩**：`vi.spyOn(API, method)` 是标准打桩边界（新增出站调用一律经 `API` class，少数历史直连 `fetch` / `EventSource` 尚未收编）；`api.ts` 本体测试用手写 fetch/Response stub；不引入 msw。禁止整模块 `vi.mock("@/api")` 与 `vi.mock("react-i18next")`（eslint 强制；全局 setup 已加载真实中文 i18n，整体 mock 后无法发现翻译缺失）。
- **SSE 打桩**：统一使用 `src/test/` 的共享 `FakeEventSource`，由 `API.openProjectEventStream` 的 spy 返回其实例。
- **mock 内部子组件**须属三类之一：重量级（虚拟化/动画/canvas）、有副作用（发起请求/启动定时器）、与本测试无关的纯展示；同一组件被 ≥3 个文件 mock 时上提 `src/__mocks__/`。
- **共享设施**：与被测对象无关的横切工具（`createDeferred`、`FakeEventSource`、factories）重复出现在 ≥3 个文件时上提 `src/test/`；API spy 组合豁免收编（各文件 spy 的方法组合互不相同，没有可提取的公共形状）；本地 `renderXxx` 仅在同一形状重复出现于 ≥3 个文件时上提。
- **目录与体量**：测试文件与源文件同级并放，不使用 `__tests__/` 目录；「一文件一被测对象」、分裂命名禁令与 3000 行熔断三条与后端一致，允许语义化主题后缀（如 `ShotDetail.drama.test.tsx`）。
- **可测性改造**：不得改变生产行为；允许抽纯函数、抽 hook 级的结构性抽取。
- **配置与 lint**：`testTimeout` 用 vitest 默认 5s，个别慢用例显式覆写并说明；eslint 启用 vitest、testing-library、jest-dom 插件（`expect-expect` 检出零断言用例）；裸 `toHaveBeenCalled` 不设禁令，断言强度归 review。

## 代码质量

工具报出的问题一律改代码，不加 baseline 或计数阈值。抑制注释只用于工具已确认的误报，且必须行内带理由：ruff 写 `# noqa: X -- 理由`，basedpyright 写 `# pyright: ignore[X]  # 理由`（典型场景是第三方 untyped 库；装饰器注册块内重复的同一条误报例外——无论块内是一条还是数十条，理由统一写在块开头一条注释里，不在每行重复，见下方「类型检查」节的 `reportUnusedFunction`），zizmor 写 `# zizmor: ignore[X] 理由`（放在被报告的 YAML 键所在行），actionlint 见下方「Workflow 语法与安全」，deptry 见下方「依赖卫生」，knip 写导出上方的 `/** @public 理由 */`，ESLint 见下方「ESLint disable 使用规范」。策略阈值类 finding（如 Dependabot 冷却期天数）按工具要求调整配置，不用豁免绕过。

**Lint & Format（ruff）：**

```bash
uv run ruff check . && uv run ruff format .
```

- 规则集：`E`/`F`/`I`/`UP`/`B`/`C4`/`PIE`/`PT`/`RET`/`PERF`/`RUF`/`SIM`/`ASYNC`；忽略 `E402`、`E501` 与 `RUF001`-`RUF003`（中文全角标点被判「易混淆 unicode」，对中文仓库是噪音）
- FastAPI 的 `Depends`/`Query` 等在参数默认值处调用属惯用法，已登记进 `flake8-bugbear.extend-immutable-calls`，无需逐处 `noqa: B008`
- line-length：120
- CI 中强制检查：`ruff check . && ruff format --check .`

**类型检查（basedpyright）：**

```bash
uv run basedpyright --warnings
```

- standard 模式 + `reportMissingTypeStubs = false`；`--warnings` 让 warning 也返回非 0，CI 与 pre-push hook 均以此执行全量扫描，闸门判据是 error 与 warning 双零
- 额外开启 `reportUnnecessaryIsInstance` / `reportUnnecessaryComparison` / `reportUnnecessaryTypeIgnoreComment` / `reportUnusedImport` / `reportUnusedVariable` / `reportUnusedClass` / `reportUnusedFunction` / `reportUnreachable` / `reportDeprecated`，级别 warning
- `reportUnreachable` 不用忽略注释绕过：穷尽分支后的防御性兜底改 `assert_never(x)`，真正的死分支删除；回调里产出、外层消费的结果用单元素列表当信箱，别写 `x: T | None = None` + `nonlocal`（basedpyright 不跟踪回调里的赋值，会把外层的空值判定当成恒真）
- `reportUnusedFunction` 把函数作用域内的任何符号一律判为私有，装饰器就地注册的处理器（`@app.exception_handler`、`@router.*`、`@server.tool`、`@event.listens_for`）因此被误报：逐个挂 `# pyright: ignore[reportUnusedFunction]`，并在注册块开头写一条注释说明理由。模块级的 `_` 前缀函数若被别的模块 import，改公开名而不是加豁免；模块级 pytest fixture 同理取公开名（由 pytest 按名收集、无人 import，本规则一律判它未被访问）
- tests/ 内 `reportOptional*`、`reportArgumentType`、`reportAttributeAccessIssue` 等设为 `none`（不做闸门）：测试的断言式访问与 mock 返回值 narrow 噪声大。scripts/ 与 alembic/ 的 `reportMissingImports` 同理，两处都用 `sys.path` 注入或运行期注入符号，静态不可解析
- 标注表达期望、判定负责实际：从磁盘 JSON 重建的数据类，其构造期形状校验走 `lib/schema_guards.py`（谓词以 `object` 收参），不要写成对自身标注的同义反复；只校验外层容器类型的访问器把元素标注写成 `Any`，别写成 `dict[str, Any]`

**Import 分层契约（import-linter）：**

```bash
uv run lint-imports
```

- 校验 `lib.config < lib.*_backends < lib.custom_provider` 分层契约，是 CI backend-static 的必过步骤
- 新增 ignore 条目前先确认该依赖边无法直接消除（约定见 `pyproject.toml`）

**依赖卫生（deptry）：**

```bash
uv run deptry lib server alembic scripts tests
```

- 直接 import 的第三方包必须出现在 `pyproject.toml` 的依赖声明里（`DEP003`）；声明了却无 import 的包必须删除或登记为运行时插件（`DEP002`）
- 只在 `fastapi` / `pydantic` 未 re-export 时才直接依赖底层包（`starlette` / `pydantic_core`），且该底层包须显式声明
- 单点误报写行内 `# deptry: ignore[X]  # 理由`；`DEP002` 没有 import 行可标注、`DEP004` 的命中散布在数百处，这两类只能写 `[tool.deptry.per_rule_ignores]`，按规则精确列出包名并每项一行注释说明理由。任何情形都不用整条规则关闭的 `ignore`
- 扫描范围含 `tests/` 与 `scripts/`，deptry 无法按目录区分 dev 与生产文件，因此测试框架包逐项进 `DEP004` 豁免，其余 dev 依赖仍被守护
- CI 中是 `backend-static` job 的 `Dependency hygiene (deptry)` step

**Workflow 语法与安全（actionlint + zizmor）：**

```bash
uv run pre-commit run --all-files actionlint && uv run pre-commit run --all-files zizmor
```

- actionlint 只扫 `.github/workflows/`，校验语法与 `run:` 脚本；本机有 shellcheck 时一并检查 shell，无则只做语法检查，CI 的官方容器镜像自带 shellcheck 与 pyflakes
- zizmor 默认 persona，`--min-severity medium` 为阻断线，本地与 CI 都显式 `--offline`——它优先于环境里的 `GH_TOKEN` / `GITHUB_TOKEN`，两处因此判定一致，代价是 impostor-commit 等在线 audit 不生效；除 workflow 外还覆盖 `.github/actions/*/action.yml` 与 `.github/dependabot.yml`，CI 的目录扫描另含 `.pre-commit-config.yaml`
- actionlint 没有行内豁免语法，唯一被允许的形式是 `.github/actionlint.yaml` 的 `paths.<文件>.ignore` 按错误消息正则精确列出，每条附一行注释说明理由
- CI 中是 `workflow-static` job，只在 workflow 域变更时运行
- 两个工具的版本写在 `.pre-commit-config.yaml` 与 `.github/workflows/test.yml` 两处，升级须同步

**Lint（前端 ESLint）：**

```bash
cd frontend && pnpm lint          # 检查
cd frontend && pnpm lint:fix      # 自动修复可修复的问题
```

- 配置：`frontend/eslint.config.js`（flat config）
- 规则集：`typescript-eslint/recommendedTypeChecked` + `react/recommended` + `react-hooks/recommended` + `jsx-a11y/recommended`
- typed linting 启用 `projectService: true`，可检查 `no-floating-promises`、`no-misused-promises` 等 async 相关问题
- CI 中强制检查：`frontend-static` job 的 `Lint` step

**未使用文件 / 导出 / 依赖（前端 knip）：**

```bash
cd frontend && pnpm knip
```

- 配置：`frontend/knip.json`；范围仅 `frontend/`，文档站不纳入
- `ignoreExportsUsedInFile: true`：只在定义文件内被使用的导出不算未使用，「类型定义即模块接口」的写法无需去 `export`
- 入口全部由 knip 的 vite / vitest 插件推导（`index.html` → `src/main.tsx`、`vitest.config.ts` → `src/test/setup.ts`），`entry` 无须声明
- `project` 含 `src/**/*.css`，Tailwind 的 `@import "tailwindcss"` 因此被算作依赖引用；含根级 `*.{ts,js}`，vitest / vite / eslint 配置因此进图
- 按需加载的 i18n 资源由 `import.meta.glob` 表达，knip 原生解析，不需要 ignore
- 依赖误报的处理顺序是先把真实入口写进 `entry` / `project`，`ignoreDependencies` 是最后手段
- CI 中强制检查：`frontend-static` job 的 `Knip` step；`pnpm check` 里排在 `lint` 与 `vitest` 之间

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

注释、ADR、CONTEXT.md、skill、issue / PR 正文按专业技术文风。

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

上站页面另在 frontmatter 用 `update_docs` 声明文档刷新流程的覆盖档位，判据见 `.agents/skills/update-docs/SKILL.md`。

| 页面 | 应该包含 | 不应该包含 |
|---|---|---|
| `README.md` | 产品定位、核心价值、最短上手路径 | 完整模型清单、所有环境变量、内部实现细节 |
| `website/docs/index.mdx` | 文档站定位、主要入口和导航概览 | 具体功能的完整操作步骤 |
| `website/docs/guide/getting-started.md` | 从部署到第一条成片的完整操作路径 | 生产级反向代理和备份策略 |
| `website/docs/guide/workflows.md` | 创作类型、生成模式、内容确认节点、选择建议 | 供应商密钥和运维命令 |
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
- **文档变更应与功能变更一起提交**：新增创作类型或生成模式、新增供应商或媒体能力、部署目录/端口/环境变量变化、数据目录/备份方式/迁移行为变化、对外 API/许可证或商业使用方式变化，均须同步更新对应文档。
- **上站 `.md` 不能使用 JSX / import**：`website/docusaurus.config.ts` 设 `markdown.format: "detect"`，`.md` 按 CommonMark 解析而非 MDX：两者都不会报编译错误，但也都不会按 MDX 语法执行——JSX 标签被当作原始 HTML 原样输出（带子内容的标签，子内容会直接显示成页面文本），import 语句被当作普通文本原样显示。需要 JSX 的页面改用 `.mdx`。

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

**本仓库不使用破坏性变更标记。** 前后端同仓一体发布，后端 API 不做版本化对外承诺——自带前端随版本同步演进，外部集成通过 `/agent-installation-guide.md` 获取当前安装入口、不依赖版本号；变更外部 Agent 的安装方式时同步更新 `public/agent-installation-guide.md`。接口删改按 `fix`/`refactor` 正常分类，不加 `!` 后缀、不写 `BREAKING CHANGE:` footer。误标合并后的纠正按 merge 方式处理：普通 squash PR 编辑正文追加 `BEGIN_COMMIT_OVERRIDE`/`END_COMMIT_OVERRIDE` 块，等待下一次 main push 或手动重跑 workflow；AFK rebase stage 则在最后一次 main push 更新 Release PR 后，直接校正其版本与 changelog 产物并通过完整性校验，再合并 Release PR。0.x 阶段的 `bump-minor-pre-major` 仅把误标的版本跃迁限制为 minor，不修正 changelog。

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
