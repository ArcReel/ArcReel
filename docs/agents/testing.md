# 运行测试

改动时先跑相关测试。push 前按 [AGENTS.md](../../AGENTS.md)运行受影响域的完整闸门。选中 0 条用例时，把范围扩大到对应目录或完整套件。

## 选择相关测试

| 改动 | 开发循环 |
| --- | --- |
| 后端测试 | 将改动的测试文件传给 `uv run python -m pytest`。 |
| 后端源码 | `uv run python -m pytest --testmon`。首次建库或改动面大时追加 `-n 4 --dist loadfile`。 |
| 后端共享面 | 改项目配置、依赖锁、根 conftest、含行为的包初始化、Alembic、profile、DB、i18n 或共享测试设施，跑完整后端测试。影响范围不明时同样跑全量。 |
| 前端测试或源码 | 在 `frontend/` 用 Vitest 跑变更的测试文件，或用 `pnpm exec vitest related --run <源文件>` 选关联用例。分支级检查用 `pnpm exec vitest run --changed <base>`。改 TypeScript 源码另跑完整 typecheck。 |
| 前端共享面 | 改包清单、锁文件、Vitest 配置、测试 setup、i18n 或 branding，在 `frontend/` 跑 `pnpm check`。 |
| 市场或目录子包 | 在各自 `packages/` 的包目录跑 `uv run python -m pytest`，再按主仓调用方补跑相关测试。消息键变化另跑主仓 `tests/unit/lib/i18n/`。 |

testmon 的覆盖数据保存在 `.testmondata` 中，不包含另起进程执行的代码；改动这类入口时需按路径手动补跑对应测试。需要缩小范围时可以传入目录。`::`、`-k`、`-m` 用于人工定位，传入后会关闭 testmon 的用例筛选。

修改页面套件、布局、滚动、请求集合或 SSE 建连时，在 `frontend/` 下跑 `pnpm e2e <相关 spec 文件>...`。若本机与 CI 结果不一致，按[贡献指南](../../CONTRIBUTING.md)中的官方镜像方式复现。后端接口形状或 `tests/fixtures/catalog.json` 变更后，执行 `pnpm e2e:record`；录制脚本或录制后端变更后，执行 `pnpm e2e:record:check`。录制与断言边界见[测试规范](../standards/testing.md)。

## Worktree 与测试数据

- 新 worktree 在后端执行 `uv sync`，在前端、文档站各自执行 `pnpm install --frozen-lockfile`。可从主检出复制 `.testmondata`，代码指纹有变化的用例会重跑。
- 根 [conftest](../../tests/conftest.py)设置 `PYTHON_DOTENV_DISABLED=1`，测试环境无需空 `.env`。涉及 `local_port` 的用例需在允许绑定本地端口的环境运行。
- 模型目录测试使用 [catalog fixture](../../tests/fixtures/catalog.json)，按 fixture 日期判定关停等生命周期。用 [catalog_support](../../tests/catalog_support.py) 构造用例数据或切换日期。验证出厂快照本身用 [test_factory_snapshot](../../tests/unit/lib/catalog/test_factory_snapshot.py)。发布数据格式由目录构建侧校验，发版刷新快照不应使普通用例失败。

## 事件循环阻塞

[阻塞守卫](../../tests/blocking_guard/__init__.py)记录 lib/server 异步调用链上的阻塞，报错列出最近业务函数和调用栈。即使阻塞发生在替身里，也按生产线程契约处理。能用内存替身的就用内存，确需落盘的替身把 I/O 放进 `asyncio.to_thread`。

[豁免清单](../../tests/blocking_guard/exemptions.toml)只收应用启动或关停时的一次性阻塞，每项附理由并由一条 `tests/unit` 用例触发。完整测试或 CI unit job 会检查未触发的豁免。对应路径删改时同步移除失效条目。

## CI 挂起诊断

先读后端 CI 的「Hang and crash diagnostics」。从 controller 的 `.progress` 定位退出码或信号，再用 worker 的最后一条进展定位用例、收尾或关停阶段。`.stacks` 有挂起和 `os._exit` 的转储，单独的信号文件记录部分终止场景，段错误还需查测试 stderr。worker 无栈退出时核对内核 OOM 记录。需要在本地复现时设置 `ARCREEL_TEST_HANG_DUMP_DIR`，输出格式见 [hang_watchdog](../../tests/hang_watchdog.py)。
