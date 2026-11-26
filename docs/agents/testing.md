# 运行测试

边界选型与替身规则见[测试规范](../standards/testing.md)。开发期间运行 `uv run python scripts/gate.py --changed [BASE]` 按相对共同祖先的改动选域快检；push 前运行 `uv run python scripts/gate.py --changed --full`。各域的触发路径与执行步骤通过 `uv run python scripts/gate.py --list` 查看。仅全量后端与前端测试跨 worktree 争夺机器锁，静态检查与定向测试以单 worker 并行运行。

## 按任务选择验证入口

| 任务或改动面 | 验证入口与盲区说明 |
| --- | --- |
| 后端源码或单测 | 改测试传给 `uv run python -m pytest <文件>`；改源码跑 `uv run python -m pytest --testmon`。`.testmondata` 不跟踪子进程执行的代码，改动子进程入口需显式跑对应测试；传入 `::`、`-k`、`-m` 会关闭 testmon 筛选。 |
| 市场或目录子包 | 在 `packages/<子包>/` 下跑 `uv run python -m pytest`，再补跑主仓调用方；消息键变化另跑 `tests/unit/lib/i18n/`。 |
| 前端源码或组件测试 | 在 `frontend/` 下跑变更的测试文件或 `pnpm exec vitest related --run <源文件>`；改 TypeScript 源码补跑 `pnpm typecheck`。 |
| 页面布局、响应式与录制契约（L1） | 修改页面套件、断点布局、无障碍、滚动、请求集合或 SSE 建连时，在 `frontend/` 下跑 `pnpm e2e <spec>...`（像素截图基线已退役，见 [ADR 0130](../adr/0130-retire-browser-pixel-baselines.md)）。后端接口形状或 `tests/fixtures/catalog.json` 变更后跑 `pnpm e2e:record` 更新替身，并通过 `uv run python scripts/gate.py e2e-freshness` 校验单录新鲜度；录制脚本、种子或归一化变更后跑 `uv run python scripts/gate.py e2e-record` 校验双录确定性。 |
| 跨层读写与任务流全栈实机（L2） | 修改跨前后端的写入持久化、任务入队与收尾、剧本确认、SSE 推送或项目迁移时，运行 `uv run python scripts/gate.py e2e-live`（单点调试可在 `frontend/` 下跑 `pnpm e2e:live <spec>`）。多 worktree 并行时用 `E2E_PORT` 指定空闲端口。失败时在 `frontend/test-results/` 查 `trace.zip`，CI 另归档 `e2e-live-diagnostics`。 |
| 新增 UI 交互、跨层自证与冒烟（L3） | 新增或修改 UI 交互与跨层链路、执行本地 QA 验收或部署实例冒烟时，调用 [`verify-arcreel`](../../.agents/skills/verify-arcreel/SKILL.md)（本地通过 `uv run python scripts/local_instance.py start` 拉起隔离实例并用 `agent-browser` 操作回读）。 |

本地运行 `pnpm e2e` 或 `pnpm e2e:live` 时，Playwright 会在启动服务前构建前端；`gate.py e2e-live` 先构建一次 `frontend/dist` 再以 `E2E_PREBUILT=1` 复用。CI 浏览器作业统一通过 [restore-browser-build](../../.github/actions/restore-browser-build/action.yml) 校验并恢复同次运行的构建产物。PR 中由 `frontend-static` 构建，Nightly 中由 `browser-build` 构建。

## 属性测试与反例重放

后端不变量套件标记 `pytest.mark.invariant`，前端属性测试在仓库根目录运行 `pnpm --dir frontend exec vitest run .property.test`；随机深度探索分别设置 `HYPOTHESIS_PROFILE=nightly` 与 `FC_PROFILE=nightly`。

前端失败报告会输出收缩后的输入与完整重放命令。重放必须在同一提交和锁文件下从仓库根目录原样执行：保留 `FC_PROFILE`、`FC_SEED` 和 `FC_PATH`，命令模型还需保留 `FC_REPLAY_PATH`。确认回归后，将最小事件或动作序列连同业务断言固化为确定性用例，命令模型一并记录重放参数。每次生成或收缩都必须重建投影器，或重置任务 store 与刷新队列，防止跨样例残留状态。

## Nightly 不变量与旧包升级

在仓库根目录运行 `uv run python scripts/nightly_invariants.py backend`（或将末尾参数换为 `frontend`、`archives`）。`backend` 按 `invariant and not released_archive` 收集根仓不变量用例，并自动发现已在自身 `pyproject.toml` 的 `tool.pytest.ini_options.markers` 中登记 `invariant` 标记的 uv workspace 子包（子包加载 `tests.invariant_profiles` 即可共用相同的 Nightly 预算）；`frontend` 收集并逐个运行 `frontend/src/**/*.property.test.*`；`archives` 按 `released_archive` 标记运行旧发行版导出包升级用例。真实发行版导出包存放在 `tests/fixtures/released_archives/`，每个 ZIP 都会经过归档导入、升级及二次迁移稳定性检查，针对特定版本的回归断言同样标记 `released_archive`。

任一已登记套件收集到零条用例或存在跳过用例时，命令均会失败。Nightly 运行告警会链接到失败作业，对应的诊断产物保留完整测试日志。复现时需在同一提交下，从仓库根目录执行日志中的 `Replay:` 重放命令：Hypothesis 使用该次探索生成的 `--hypothesis-seed` 固定随机种子，fast-check 则按失败报告中收缩后的种子与路径重放。

## Worktree 与测试数据

- 新 worktree 在根目录执行 `uv sync`，在 `frontend/` 和 `website/` 各自执行 `pnpm install --frozen-lockfile`。首次跑 `--testmon` 前可从主检出或其他 worktree 复制 `.testmondata`，代码指纹有变化的用例会自动重跑。
- 根 [conftest](../../tests/conftest.py) 设置 `PYTHON_DOTENV_DISABLED=1`，测试环境无需空 `.env`。涉及 `local_port` 的用例需在允许绑定本地回环端口的环境运行。
- 进入真实应用 lifespan 的测试（包括以 `with TestClient(app)` 启动应用的 fixture）可显式依赖 `app_lifespan_stubs`，跳过宿主沙箱探测，并将官方目录同步、素材入库和远端素材维护替换为空任务。
- 模型目录测试使用 [catalog fixture](../../tests/fixtures/catalog.json)，按 fixture 日期判定关停等生命周期。用 [catalog_support](../../tests/catalog_support.py) 构造用例数据或切换日期；验证出厂快照本身用 [test_factory_snapshot](../../tests/unit/lib/catalog/test_factory_snapshot.py)。

## 源码注释检查

`uv run python -m scripts.audit_comments` 检查相对 `HEAD` 与 `origin/main` 共同祖先的新增源码注释（覆盖已提交、未提交和未跟踪文件，可用 `--base <commit>` 指定基线，`--stats` 盘点注释与可整行删除行数）。规则与工具指令边界见 [ADR 0121](../adr/0121-source-comment-ratchet-and-decision-records.md)。

## 事件循环阻塞

[阻塞守卫](../../tests/blocking_guard/__init__.py)记录 `lib/` 与 `server/` 异步调用链上的同步阻塞，报错列出最近业务函数和调用栈。即使阻塞发生在测试替身里，也按生产线程契约处理：优先用内存替身，确需落盘的替身把 I/O 放进 `asyncio.to_thread`。

[豁免清单](../../tests/blocking_guard/exemptions.toml)只收应用启动或关停时的一次性阻塞，每项附理由并由一条 `tests/unit` 用例触发。完整测试或 CI unit 作业会检查未触发的豁免；对应路径删改时同步移除失效条目。

## CI 覆盖率与 Nightly 告警

普通 PR 和 `main` 推送按改动选择测试域。前端或后端域变更（包括共享协议、Alembic 和项目迁移）必须通过 `e2e-live`。聚合检查 `ci-required` 逐项核对各域应跑作业，任何必跑作业被跳过都会失败。Nightly 每次运行全部实机用例，并将构建和实机作业结果纳入告警汇总。PR 的 `changes` 作业记录受测合并提交，摘要列出改动涉及的静态门禁文件。对于仅修改产品源码或站点文档的推送，`main` 核对已合并 PR、受测树、合并父提交以及各受影响域的实际作业和步骤结果。证据缺失、基线前移、门禁变更或应跑项被跳过时，重新运行受影响域。

每周日 02:00 UTC、推送 `v*` 标签或手动运行 `weekly-full.yml` 时，Tests 工作流通过 `full: true` 运行全部域。`release-please--*` 发布 PR 自动使用同一范围。巡检包括容器健康检查、PostgreSQL 16、17、18 兼容测试和发布完整性检查。普通验证与全域巡检均关闭覆盖率采集。

Nightly 将后端测试分片采集为 `coverage-<编号>/raw.coverage`。各片通过后，`backend-report` 核对分片并生成一份后端 XML 报告。前端和真实 PostgreSQL 的覆盖率分别上传至 `frontend` 和 `postgres` 标记。新增每夜深度检查时，在 `nightly-full.yml` 定义作业并将其 ID 加入 `report.needs`。汇总作业读取每个直接依赖的 `result`，仅在全部成功时执行恢复流程。矩阵作业使用 `fail-fast: false`，失败时保留诊断。

Nightly 失败按运行编号创建或追加告警，并按失败作业给出产品断言、测试竞态、容量或基础设施的初步分类；确认问题后可另开 issue 或为告警添加 `bug` 等标签保留。Nightly 恢复通过后，自动关闭同一 Git 引用下仅带 `ci` 标签且保留原始标记的本次及更早告警。

## CI 挂起诊断

后端 CI 挂起或崩溃时查看「Hang and crash diagnostics」：从 controller 的 `.progress` 定位退出码或信号，再用 worker 的最后一条进展定位用例、收尾或关停阶段。`.stacks` 保存挂起和 `os._exit` 的转储，段错误需查测试 stderr，worker 无栈退出时核对内核 OOM 记录。本地复现设置 `ARCREEL_TEST_HANG_DUMP_DIR`，输出格式见 [hang_watchdog](../../tests/hang_watchdog.py)。
