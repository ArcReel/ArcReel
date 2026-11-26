# 运行测试

开发时运行 `uv run python scripts/gate.py --changed [BASE]`。闸门相对共同祖先选域，包含未提交、未跟踪和重命名前后的路径。各域保留完整静态检查，后端按 testmon 与改动的测试文件筛选用例，前端用 Vitest 运行关联测试与改动的测试文件。

共享配置、锁文件、测试设施、迁移、跨语言契约、子进程入口、删除或无法识别的域内路径会升级至对应域完整测试。无法归属的代码路径触发前后端完整测试。定向测试零命中时重跑该域完整测试。如果后端 testmon 库中没有失败用例，且通过的用例已覆盖所有改动源码的当前指纹，则跳过全量重跑。后端首次建 testmon 库也按全量步骤排队。

push 前运行 `uv run python scripts/gate.py --changed --full`。显式点名的域始终完整运行。仅全量后端和前端测试共用机器锁，定向测试使用单个 worker。

## 选择相关测试

| 改动 | 开发循环 |
| --- | --- |
| 后端测试 | 将改动的测试文件传给 `uv run python -m pytest`。 |
| 后端源码 | `uv run python -m pytest --testmon`。首次建库或改动面大时追加 `-n 4 --dist loadfile`。 |
| 后端共享面 | 改项目配置、依赖锁、根 conftest、含行为的包初始化、Alembic、profile、DB、i18n 或共享测试设施，跑完整后端测试。影响范围不明时同样跑全量。 |
| 前端测试或源码 | 在 `frontend/` 用 Vitest 跑变更的测试文件，或用 `pnpm exec vitest related --run <源文件>` 选关联用例。分支级检查用 `pnpm exec vitest run --changed <base>`。改 TypeScript 源码另跑完整 typecheck。 |
| 前端共享面 | 改包清单、锁文件、Vitest 配置、测试 setup、i18n 或 branding，在 `frontend/` 跑 `pnpm check`。 |
| 市场或目录子包 | 在各自 `packages/` 的包目录跑 `uv run python -m pytest`，再按主仓调用方补跑相关测试。消息键变化另跑主仓 `tests/unit/lib/i18n/`。 |

testmon 的覆盖数据保存在 `.testmondata` 中，不包含另起进程执行的代码；手动运行 testmon 时，改动这类入口需单独补跑对应测试。需要缩小范围时可以传入目录。`::`、`-k`、`-m` 用于人工定位，传入后会关闭 testmon 的用例筛选。

修改页面套件、布局、滚动、请求集合或 SSE 建连时，在 `frontend/` 下跑 `pnpm e2e <相关 spec 文件>...`。后端接口形状或 `tests/fixtures/catalog.json` 变更后，执行 `pnpm e2e:record`；录制脚本或录制后端变更后，执行 `pnpm e2e:record:check`。录制与断言边界见[测试规范](../standards/testing.md)。

本地运行 `pnpm e2e` 或 `pnpm e2e:live` 时，Playwright 会在启动服务前构建前端。CI 浏览器作业复用 `frontend-static` 的构建产物：检出同一提交后由 [restore-browser-build](../../.github/actions/restore-browser-build/action.yml) 校验产物来源与构建输入，恢复 `dist` 及运行依赖并设置 `E2E_PREBUILT=1`。

### 全栈实机 E2E

在仓库根目录运行 `uv run python scripts/gate.py e2e-live`。首次运行前先在 `frontend/` 安装依赖，再执行 `pnpm exec playwright install chromium`。可与其他域组合运行，例如 `uv run python scripts/gate.py workflows e2e-live`。门禁每次构建一次前端，再用本次生成的 `dist` 运行实机用例。并行使用多个 worktree 时，用 `E2E_PORT` 指定空闲端口，默认端口为 4174。

实机套件使用桌面视口和单个 worker，后端通过 `/health` 检查后运行用例。每次启动使用临时 SQLite 数据库、真实生成 worker 和 SSE，关闭认证，外部付费服务使用本地替身。用例失败时，在 `frontend/test-results/` 查找 `trace.zip`。CI 的 `e2e-live-diagnostics` 产物还包含后端与测试运行日志及 Playwright HTML 报告。

## 前端属性测试与反例重放

在仓库根目录运行 `pnpm --dir frontend exec vitest run .property.test`。默认 `ci` 档使用固定种子 4161，每项属性运行 25 个样例、每条序列最多 16 步；`nightly` 深度探索使用随机种子，每项属性运行 200 个样例、每条序列最多 80 步：

```bash
FC_PROFILE=nightly pnpm --dir frontend exec vitest run .property.test
```

失败报告会输出收缩后的输入与完整重放命令。重放时需在同一提交和锁文件下，从仓库根目录原样执行该命令：保留 `FC_PROFILE`、`FC_SEED` 和 `FC_PATH`；命令模型还需保留 `FC_REPLAY_PATH`（普通事件数组下该值为空）。确认回归后，将最小事件或动作序列连同业务断言固化为确定性用例，命令模型一并记录对应的重放参数。每次生成或收缩都必须重建投影器，或重置任务 store 与刷新队列，避免跨样例残留状态。

## Worktree 与测试数据

- 新 worktree 在后端执行 `uv sync`，在前端、文档站各自执行 `pnpm install --frozen-lockfile`。可从主检出复制 `.testmondata`，代码指纹有变化的用例会重跑。
- 根 [conftest](../../tests/conftest.py)设置 `PYTHON_DOTENV_DISABLED=1`，测试环境无需空 `.env`。涉及 `local_port` 的用例需在允许绑定本地端口的环境运行。
- 模型目录测试使用 [catalog fixture](../../tests/fixtures/catalog.json)，按 fixture 日期判定关停等生命周期。用 [catalog_support](../../tests/catalog_support.py) 构造用例数据或切换日期。验证出厂快照本身用 [test_factory_snapshot](../../tests/unit/lib/catalog/test_factory_snapshot.py)。发布数据格式由目录构建侧校验，发版刷新快照不应使普通用例失败。

## 源码注释检查

`uv run python -m scripts.audit_comments` 会检查相对 `HEAD` 与 `origin/main` 共同祖先的源码注释，范围覆盖已提交、未提交和未跟踪的文件，也可用 `--base <commit>` 指定比较基线。该检查同时随 `gate.py conventions`、`gate.py --changed` 和 CI 运行，规则与工具指令边界见 [ADR 0121](../adr/0121-source-comment-ratchet-and-decision-records.md)。

`uv run python -m scripts.audit_comments --stats` 按语言盘点当前工作树中的注释及预计可整行删除的行数。`comment_lines` 统计含注释的行数，包括同时含代码和注释的行。`removable_lines` 估算能整行删除的行数。用户直接编辑的配置示例和部署文件可保留帮助性注释，并按同一规则从统计范围中排除。

## 事件循环阻塞

[阻塞守卫](../../tests/blocking_guard/__init__.py)记录 lib/server 异步调用链上的阻塞，报错列出最近业务函数和调用栈。即使阻塞发生在替身里，也按生产线程契约处理。能用内存替身的就用内存，确需落盘的替身把 I/O 放进 `asyncio.to_thread`。

[豁免清单](../../tests/blocking_guard/exemptions.toml)只收应用启动或关停时的一次性阻塞，每项附理由并由一条 `tests/unit` 用例触发。完整测试或 CI unit job 会检查未触发的豁免。对应路径删改时同步移除失效条目。

## CI 覆盖率与 Nightly 告警

普通 `main` 推送按改动选择测试域，后端、PostgreSQL 兼容与前端测试均跳过覆盖率采集。Nightly 和发布 PR 采集覆盖率。复用 Tests 工作流时，`full` 控制检查范围，`coverage` 控制覆盖率采集。PR 的 `changes` 作业摘要列出改动涉及的静态门禁文件，供审阅者核查。

Nightly 按运行编号创建告警，重跑失败时在同一告警追加记录。记录按失败作业给出产品断言、测试竞态、容量或基础设施的初步分类。确认问题后可另开 issue，也可为运行告警添加 `bug` 等标签将其保留。Nightly 恢复通过后，只关闭同一 Git 引用下本次及更早运行的自动告警（须保留原始告警标记与标题，且仅带 `ci` 标签）。

## CI 挂起诊断

先读后端 CI 的「Hang and crash diagnostics」。从 controller 的 `.progress` 定位退出码或信号，再用 worker 的最后一条进展定位用例、收尾或关停阶段。`.stacks` 有挂起和 `os._exit` 的转储，单独的信号文件记录部分终止场景，段错误还需查测试 stderr。worker 无栈退出时核对内核 OOM 记录。需要在本地复现时设置 `ARCREEL_TEST_HANG_DUMP_DIR`，输出格式见 [hang_watchdog](../../tests/hang_watchdog.py)。
