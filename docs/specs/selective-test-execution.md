# 改动感知测试执行 Spec

## 背景

ArcReel 后端已超过一万个 pytest 用例，前端也有大量 Vitest 用例。现有流程虽然按
backend、frontend、website、docker 四个域裁剪 CI，但域内仍是全量执行；`DEV.md`
又只列出全量命令，导致开发过程中的每个小改动都容易重复跑完整套件。

本 Spec 只调整测试的**执行时机与选择方式**，不降低测试契约，也不把“少跑测试”解释为
删除测试。测试质量审计仍按 `CONTRIBUTING.md` 的无意义测试判据单独推进。

## 目标

1. 每个可验证的小改动都执行与其存在静态依赖或显式契约关系的测试。
2. 选择器无法证明局部执行安全时，保守升级为对应域全量，不静默跳过。
3. 在任务完成、合并到 `main`、发布和 nightly 等关键节点执行全量验证。
4. 相关测试不携带全局覆盖率阈值；覆盖率只在全量节点采集并作为趋势信号。
5. 选择结果可预览、可解释、可测试，开发者和 Agent 使用同一入口。

## 非目标

- 不根据文件名猜测后直接声称测试完备。
- 不引入依赖历史覆盖数据库的选择器；临时 worktree 必须冷启动可用。
- 不在本次改动中清理重复、超长或弱断言测试。
- 不用自动重试掩盖偶发失败。

## 三层验证模型

### 1. Related：开发反馈环

每次完成一个连贯的小改动后执行：

```bash
uv run python scripts/test_changed.py --base main --run
```

选择规则：

- 直接修改的 pytest/Vitest 测试必跑。
- Python 生产模块通过 AST import 图反向遍历，执行直接或间接依赖它的测试模块。
- 前端源码交给 Vitest `related --run`，由 Vite 模块图选择测试。
- i18n、前后端共享枚举、Agent Profile 等不经过普通 import 的契约使用显式映射。
- `conftest.py`、依赖/测试配置、删除的生产模块、无法找到任何相关测试的 Python 生产模块，
  自动升级为对应域全量。
- 文档或纯静态资源没有运行期测试时明确报告，不伪造“已覆盖”。

Related 层不收集全局覆盖率，也不替代类型检查、构建和架构检查。

### 2. Domain full：任务完成闸门

实现完成、准备提交或合并前，对本任务触达的域各执行一次全量：

- backend：ruff、format check、basedpyright、import-linter、Profile lint、全部非 E2E pytest。
- frontend：typecheck、eslint、全部 Vitest；影响打包链路时再执行 build。
- website：typecheck、eslint、format、内容一致性和双语 build。
- database：影响 DB/ORM/迁移时执行 PostgreSQL 方言测试和迁移闭环。
- docker：影响 Docker 装配时构建并做健康检查。

域间契约文件可同时命中多个域；例如前端 i18n 变更同时命中后端契约测试。

### 3. Repository full：仓库关键节点

以下节点无条件执行所有域：

- push 到 `main`；
- release PR；
- nightly；
- CI workflow/action、Codecov 配置或 Git 忽略规则变化。

PR 仍执行所有受影响域的全量验证。Related 层用于开发反馈，不作为合并前唯一证据。

## Python 选择器设计

`scripts/test_changed.py` 从指定 base ref 到当前 HEAD 的 diff、暂存区、工作区和未跟踪文件
合并出变更集合。Python 模块解析仅扫描仓库内的 `lib/`、`server/`、`alembic/`、
`scripts/` 与 `tests/`，不 import 被分析模块，避免收集期副作用。

依赖边来自 `import` 和 `from ... import ...`；选择器从变更模块沿反向边传递到测试模块。
动态 import、文件内容契约和生成配置无法可靠地由 AST 推导，必须登记显式契约映射或触发全量。

安全规则固定如下：

- 依赖文件、pytest 配置、共享 conftest/fake/factory 变化：backend full。
- 生产 Python 文件被删除：backend full。
- 生产 Python 文件没有解析到相关测试：backend full，并输出原因。
- 选择器自身变化：执行选择器测试。
- 未知 backend 非 Python 文件：backend full；已登记的 Profile/模板文件走契约测试集合。

## 前端选择器设计

普通 `frontend/src` 代码使用 `vitest related --run`。直接修改的测试文件单独传给
`vitest run`，确保测试自身一定执行。以下文件变化触发 frontend full：

- `package.json`、lockfile、Vitest/Vite/TypeScript 配置；
- `src/test/**` 与 `src/__mocks__/**`；
- 删除的前端源码；
- 无法交给 Vitest 模块图解释的前端运行期文件。

## 覆盖率

覆盖率只在 CI 全量 job 生成并上传。数值不阻断合并，失败测试、类型错误、lint、构建错误和
契约错误才是闸门。这样 Related 层不会因“只执行相关测试”天然得不到全仓覆盖率而失败。

## 验收标准

1. 选择器能预览计划，也能执行计划。
2. 修改生产 Python 模块能选中直接和传递依赖测试。
3. 修改测试文件必定选中该文件。
4. 修改共享测试基础设施、依赖文件、删除生产模块或无相关测试模块时升级全量。
5. 前端源文件使用 Vitest related，前端测试文件直接执行，前端测试基础设施变化升级全量。
6. 前端 i18n/共享 workflow 类型变化能触发对应后端契约测试。
7. main push、release、nightly 和 CI 基础设施变化触发所有 CI 域。
8. `DEV.md` 明确 Related、Domain full、Repository full 的时机，禁止每个小改动重复全量。
