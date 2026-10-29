# 运行测试

改代码或测试后，开发循环先跑与改动相关的最小测试集；push 前再跑 `AGENTS.md` 中受影响域的完整闸门。

## 选择相关测试

选择结果为 0 个测试时，扩大到对应目录或完整测试集；0 个测试不算验证通过。

- **后端测试文件变更**：直接运行这些文件。
- **后端源码变更**：`uv run python -m pytest --testmon`。pytest-testmon 按 `.testmondata` 里记录的覆盖关系只跑受改动影响的用例；没有数据文件时它跑完整测试并建库，之后才是增量。首次建库或改动面大时加 `-n 4 --dist loadfile`，小选集并行反而更慢。另起进程执行的代码不进覆盖关系（如 `tests/integration/test_imports.py` 这类在子进程里 import 的用例），改动它们触达的模块时按路径补跑。给它传目录可以进一步缩小范围；带 `::` 的用例选择器、`-k`、`-m` 会让它退回不筛选，这几种只用于人工定位。
- **需要后端完整测试的改动**：`pyproject.toml`、`uv.lock`、根 `tests/conftest.py`、含行为的包初始化，或涉及 Alembic、profile、DB、i18n、共享测试设施；无法判断影响范围时也运行完整测试。
- **前端测试文件变更**：直接把文件传给 Vitest。
- **前端源码变更**：在 `frontend/` 运行 `pnpm exec vitest related --run <源文件>`；分支级检查用 `pnpm exec vitest run --changed <base>`。TypeScript 源码变更同时运行完整 typecheck。
- **前端页面级套件（`frontend/e2e/`）变更、改动布局或滚动相关的组件、改变调用的接口集合或流连接（含 SSE）的建立时机**：在 `frontend/` 运行 `pnpm e2e`。页面级套件只回放录制替身与场景 `api` 覆盖，未录制的请求直接失败；本机渲染与 CI 不一致时以容器内结果为准，运行方式见 `CONTRIBUTING.md`「前端页面级测试」。改动后端接口的响应形状时，同时运行 `pnpm e2e:record` 重录接口替身。
- **需要前端完整测试（`pnpm check`）的改动**：`package.json`、`pnpm-lock.yaml`、`vitest.config.*`、测试 setup、i18n 或 branding。
- **子包 `packages/arcreel-market-core/` 源码变更**：运行子包全部测试（`cd packages/arcreel-market-core && uv run python -m pytest`，子包自带 pytest 配置，与主仓分开收集），并按主仓的导入方选择相关测试；子包的消息键增删时同时运行 `tests/unit/lib/i18n/`。
- **子包 `packages/arcreel-catalog-core/` 源码变更**：运行子包全部测试（`cd packages/arcreel-catalog-core && uv run python -m pytest`），并按主仓的导入方选择相关测试。

## 在 worktree 里运行

worktree 没有 `.venv` 与 `node_modules`：后端先 `uv sync`，前端与文档站在各自目录 `pnpm install --frozen-lockfile`。把主检出的 `.testmondata` 复制进 worktree 可以跳过 testmon 的首次建库；文件里记录的是按代码指纹索引的覆盖关系，与当前代码不一致的部分会被当作改动重跑。标了 `local_port` 的用例在禁止绑定本地端口的沙箱里会被跳过并注明原因，完整测试需要在允许绑定端口的环境里运行一次。

## 事件循环阻塞护栏

根 conftest 加载 `tests/blocking_guard`：同步与异步用例全程记录 lib/server 代码在事件循环线程上的阻塞调用（含同步 `TestClient` 背后的应用线程），用例结束时豁免清单外的违规让该用例失败，失败信息给出条目和调用栈。调用栈由内向外列出 lib/server/tests 帧，前面按真实调用顺序附上阻塞点到第一个仓库帧之间最内层的几条非仓库帧（标准库与第三方包，路径取包内相对形式，超出的注明省略条数），用来分辨业务代码 `await` 处被调起的非仓库代码里是谁在阻塞。条目是离阻塞点最近的 lib/server async 函数（`<文件>::<限定名>`），阻塞发生在 `tests/` 下的替身里也一样：换成生产依赖后，同一位置同样会阻塞事件循环。替身误报时，按生产实现的线程契约修替身：能用内存实现的就改成内存实现；替身的职责本身就要落盘的（后端写产物、模拟并发写方、调用会写正式文件的生产回调），把这段 I/O 放进 `asyncio.to_thread`。外层没有 lib/server async 函数的阻塞（测试函数直接调用同步 lib 代码）不计。

- `tests/blocking_guard/exemptions.toml` 是永久豁免，只收应用启动与关停路径上的一次性阻塞，每条写明理由。除此之外没有豁免途径，新增的阻塞调用一律修掉。完整运行时，本次未触发的豁免条目会让会话失败：启动与关停路径改了，就同步删掉对应条目。每条豁免都必须由 `tests/unit` 下的用例触发：CI 的 unit job 按路径只跑 `tests/unit`，带 `--blocking-guard-expect-all-exemptions` 后同样做这项检查，未触发的条目报为过期；integration job 不带。

## CI 挂起诊断

后端测试 job 设了 `ARCREEL_TEST_HANG_DUMP_DIR`，根 conftest 加载的 `tests/hang_watchdog.py` 让 xdist 的 controller 与每个 worker 记下开始执行的用例，进程 180 秒无进展时转储全部线程的调用栈，会话收尾与进程退出阶段同样覆盖。测试步骤超时短于 job 超时，步骤失败或被取消后，「Hang and crash diagnostics」步骤输出每个进程最后的进展、线程栈与内核的 OOM 记录。进展末尾是 `sessionfinish`、`unconfigure` 的进程已跑完会话，末尾停在某条 `run` 用例的进程就卡在或死在那条用例上。排查挂起或 `node down: Not properly terminated` 先看这一步：宕掉的 worker 最后一条用例就是崩溃时在跑的用例，controller 的进展记有它的退出码或终止信号，按死法再找调用栈：

| 死法 | 调用栈在哪里 |
| --- | --- |
| 调用 `os._exit` | 该 worker 的 `.stacks`，抬头是 `调用 os._exit(<退出码>)` |
| 被 SIGTERM、SIGHUP、SIGINT 终止或打断 | 该进程同名的 `.SIGTERM` 等文件 |
| 段错误等致命信号 | 测试步骤 stderr 里的 `Fatal Python error` |
| 被 SIGKILL | 没有栈，看内核日志里有没有 OOM |

CI 与本地闸门的 xdist 命令带 `--max-worker-restart=0`：worker 崩溃后不补新 worker，直接失败。pytest-xdist 3.8.0 补 worker 时会把已跑完的文件重新排队，会话卡在 99%（上游 pytest-dev/pytest-xdist#1378，修复提交 25282ab）。依赖升到包含该提交的版本后，可以去掉这个参数。本地复现时设同一个环境变量即可得到同样的文件。
