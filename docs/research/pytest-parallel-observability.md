# pytest 并行与可观测性插件在本仓库测试设施下的兼容前提

调研对象：pytest-xdist（并行）、`--durations`（慢用例可观测）、pytest-timeout（挂死防护）。
结论依据为各插件官方文档与已安装源码，并逐条对照本仓库 `pyproject.toml`、`tests/conftest.py`、
`tests/agent_session_store/conftest.py`、`.github/workflows/test.yml`。**本文不做实施**。

调研基线（实证时的实际版本）：pytest 9.1.1 / pytest-asyncio 1.4.0 / pytest-cov 7.1.0 /
coverage 7.15.4 / pytest-xdist 3.8.0 / execnet 2.1.2 / pytest-timeout 2.4.0。
xdist 与 timeout 为实证临时安装，实证后已还原 `pyproject.toml` 与 `uv.lock`。

引入动机的量化依据：CI run 32223755010 中 `backend-tests` 耗时 **20m22s**，而该 job 的
`timeout-minutes` 为 **25**——余量已不足四分之一；同 run 的 `postgres-compat` 仅 2m44s。
全量收集规模为 10246 个用例（`--collect-only` 用时 1.92s）。

---

## 摘要

### 可直接采用

| 项 | 一句依据 |
| --- | --- |
| pytest-cov 与 xdist 的覆盖率合并 | 实测同一子集串行与 `-n 4` 的 `TOTAL` 完全一致（48340 stmts / 35618 miss / 26%），`--cov-fail-under` 门限仍准确；pytest-cov 官方将 xdist `load` 模式列为一等支持。 |
| `asyncio_mode = "auto"` 与 xdist 并存 | 每个 worker 是独立进程、各自建 loop，pytest-asyncio 默认 function 级 loop scope 不跨进程；本仓库全部实证子集（含 92 个 PG 用例）在 `-n 4`/`-n 8` 下全绿。 |
| `tmp_path` 系 fixture（含 autouse `_profile_env`）的 worker 隔离 | xdist 为每个 popen worker 指派 `<basetemp>/popen-gwN` 作为独立 basetemp（`xdist/workermanage.py:338-341`），tmp 目录天然不撞。 |
| 模块级单例 `generation_queue_module._QUEUE_INSTANCE` | worker 是独立进程，单例各持一份，跨 worker 不可能互相覆写；进程内的复位仍由现有 fixture teardown 保证。 |
| pytest-timeout 的 `signal` 方法 | 对 async 与 sync 用例均生效且失败可恢复（会话继续、teardown 正常）；在 `-n 2` 下同样表现为普通 failure。 |
| `--durations=N` | pytest 内置、无需插件；xdist 下由 controller 汇总 worker 上报的时长，能正常输出。 |

### 需先改设施

| 项 | 一句依据 |
| --- | --- |
| `_enforce_classification_markers` 的 `pytest.UsageError` | 在 xdist worker 内抛出会被 `xdist/dsession.py:217` 的 `assert not crashitem` 转成 `INTERNALERROR`，中文违规清单**完全丢失**，只剩一条 nodeid；需要在 CI 前置一道 `--collect-only` 闸（该模式下 xdist 不分发，消息可读、退出码 4）。 |
| worker 数的确定方式 | `-n auto` 优先用 psutil 的**物理**核数、缺 psutil 时回落到 `sched_getaffinity`（`xdist/plugin.py:16-53`）；本仓库当前无 psutil，一旦某依赖引入它，CI 的 worker 数会静默减半——须固定 `-n 4` 或 `PYTEST_XDIST_AUTO_NUM_WORKERS`。 |
| 少数用例的仓库根共享路径写入 | `tests/test_app_data_dir.py` 会在 `PROJECT_ROOT` 下创建并 `rmdir` 目录（`projects/`、`test_relative_data_dir_xyz`），`--dist load` 会把同文件用例拆到不同 worker；需 `--dist loadfile`（或 `xdist_group`）把它们钉在同一 worker。 |
| `postgres-compat` 若要并行 | 92 个用例里 83 个走 `tests/agent_session_store/conftest.py` 的 per-test schema（天然隔离），仅 `tests/test_agent_credential_repo.py` 的 9 个用例共用 alembic 建出的 `public` schema；并行前需 `--dist loadfile` 或 per-worker database。 |

### 不建议

| 项 | 一句依据 |
| --- | --- |
| `--timeout-method=thread` | 该方法以 `os._exit(1)` 硬杀进程，不做 fixture teardown、不出 XML 报告；实测后续用例直接不执行，在 xdist 下等价于 worker crash。 |
| 把 `-n auto` / `-n 4` 写进 `addopts` | 小规模选集在并行下**更慢**（45 个用例：1.81s → 3.56s；629 个用例：5.20s → 5.76s），而本地调试几乎都是小选集，且并行会打散 `-x`/`--pdb` 的定位体验。 |
| 并行 `postgres-compat` job | 该 job 仅 92 个用例、CI 上 2m44s，可省的绝对时间小于共享 `public` schema 带来的行锁等待与偶发死锁风险。 |
| 依据并行下的 `--durations` 数值做性能基线 | 同一用例的 setup 时长在 `-n 4` 下从 0.38s 涨到 0.72s（CPU 争抢），数值只可作相对排序，绝对值须串行采集。 |

---

## 逐点核对

### 1. `asyncio_mode = "auto"`（pytest-asyncio ≥1.3）与 xdist 的 event loop / fixture scope 交互

**来源**：pytest-asyncio 官方 `docs/reference/configuration.md` 与 `docs/concepts.md`——`asyncio_mode`
只决定「哪些 async 函数被当作 asyncio 测试收集」，不涉及进程模型；loop 的生命周期由
`asyncio_default_fixture_loop_scope` / `loop_scope` 控制，默认 function 级。
xdist 的并行单位是**进程**（execnet gateway，见 `xdist/workermanage.py`），不是线程或协程。

**本仓库对照**：`pyproject.toml` 只设了 `asyncio_mode = "auto"`，未设
`asyncio_default_fixture_loop_scope`，即全部 fixture 与用例都在各自的 function 级 loop 上跑。
function 级 loop 不存在跨进程共享的可能，因此 xdist 不引入任何 loop 冲突。

一个真实相关的设计已经在 `tests/conftest.py::async_session` 的 PG 分支里写明：用 `NullPool`
避免 asyncpg 连接被跨 loop 复用。这条约束在并行下**依然只与 function 级 loop 有关**，不因
worker 增多而变化——每个 worker 串行执行分给它的用例，同一时刻只有一个活跃 loop。

**结论**：可直接采用。实证支持见「实证记录」中全部子集在 `-n 4` 下全绿。
若将来把 `asyncio_default_fixture_loop_scope` 改为 `session`，则需重新评估——session 级 loop 在
xdist 下会变成「每 worker 一个 session loop」，session 级 async fixture 的初始化副作用会被执行 N 次。

### 2. `[tool.coverage.run] concurrency = ["greenlet", "thread"]` 与 xdist 下的覆盖率合并

**来源**：pytest-cov 官方 README 的 "Distributed testing" 一节明确：`--dist load` 模式下
「coverage is measured on every worker and combined automatically into a single report」。
coverage.py 的 `concurrency` 描述的是**进程内**的并发原语（greenlet / thread / gevent /
eventlet / multiprocessing），用于让 tracer 正确跟随协程或线程切换；它与「跨进程合并」是两件事，
后者由 pytest-cov 负责（worker 结束时把 coverage 数据回传 controller 合并）。

**本仓库对照**：现配置 `concurrency = ["greenlet", "thread"]` 覆盖的是 SQLAlchemy asyncio
（greenlet）与 `asyncio.to_thread` / TestClient 的线程池。xdist 用的是 execnet 独立进程，
**不是** `multiprocessing`，因此不需要往 `concurrency` 里加 `multiprocessing`——加了反而会让
coverage 去 patch multiprocessing 的启动路径，属无谓风险。

**实证**：`tests/agent_session_store` 子集，串行与 `-n 4` 两次运行的汇总行逐字相同：

```
TOTAL   48340   35618   26%
```

**结论**：可直接采用，`--cov-fail-under=80` 在并行下仍是准确门限。
附带观察：加 `--cov` 后同一子集从 3.56s 涨到 38.46s，绝大部分开销在**报告生成**
（对 48340 行源码做 term + xml 两份报告），与是否并行无关——并行不会放大也不会削减这块成本。

### 3. `tests/conftest.py` 的 fixture 与模块级单例在多 worker 下的隔离性

| 设施 | 跨 worker 隔离性 | 依据 |
| --- | --- | --- |
| `async_session`（SQLite 分支） | 安全 | 每个用例新建 `sqlite+aiosqlite:///:memory:` 引擎，内存库随连接私有，进程间不可见。 |
| `async_session`（PG 分支） | 见 §4 | 共享 alembic 建出的 `public` schema，靠外层事务 + `join_transaction_mode="create_savepoint"` + teardown `ROLLBACK` 保证不落盘。 |
| `db_factory` / `meta_store` / `generation_queue` | 安全 | 同为 per-test in-memory SQLite 引擎。 |
| `generation_queue_module._QUEUE_INSTANCE` | 安全（且更安全） | worker 是独立进程，单例各持一份；跨 worker 无共享内存。 |
| autouse `_profile_env`（写 `tmp_path/agent_runtime_profile`） | 安全 | xdist 给每 worker 指派 `<basetemp>/popen-gwN`（`xdist/workermanage.py:338-341`），`tmp_path` 路径天然带 worker 维度。 |
| autouse `_stub_sandbox_check` | 安全 | 纯 `monkeypatch.setattr`，作用域是进程内模块属性，按用例回滚。 |
| autouse `_reset_app_data_dir_cache` | 安全 | 复位的是进程内 `functools.cache`。 |
| `tests/agent_session_store/conftest.py::session_factory`（PG 分支） | 安全 | schema 名带 `uuid4().hex[:12]`，per-test 建/删，跨 worker 不撞。 |
| `tests/agent_session_store/conftest.py::file_session_factory` | 安全 | 库文件落在 `tmp_path`，随 worker basetemp 隔离。 |
| alembic 系用例（`tests/test_alembic_*.py`） | 安全 | 各自 `monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///<tmp_path>/test.db")`，不碰共享库。 |

**唯二需要处理的跨 worker 共享面**：

1. `tests/test_app_data_dir.py` 直接在 `PROJECT_ROOT` 下创建目录并在 `finally` 里 `rmdir`
   （`projects/`、`test_relative_data_dir_xyz`）。同文件内多个用例都会 touch `PROJECT_ROOT/projects`，
   `--dist load` 不保证它们落在同一 worker，两个 worker 交错的 `mkdir` / `rmdir` 存在竞态。
2. `tests/test_auth_coverage.py` 的 module 级 autouse `_auth_env`（`patch.dict(os.environ, ...)`）
   在 `--dist load` 下会被多个 worker 各建一份。这不产生错误（环境变量是进程内的），只是重复开销；
   但它是「模块内用例假定共享同一份 setup」这一模式的样板，后续若有人在 module 级 fixture 里放
   非幂等副作用，`--dist load` 会先坏在这里。

两者都由 `--dist loadfile` 直接消解（同文件用例必落同一 worker，见 xdist `docs/distribution.md`）。

### 4. `postgres-compat` 单库多 worker 是否会互相污染

**当前构成**（`-m "uses_db and not sqlite_only"` 收集出 92 个用例，分布在 11 个文件）：

- 10 个文件走 `tests/agent_session_store/conftest.py::session_factory`，PG 下每个用例建一个
  `test_<uuid12>` schema、用 `search_path` 指过去、teardown `DROP SCHEMA ... CASCADE`。
  这是**已经并行就绪**的隔离方式。
- 只有 `tests/test_agent_credential_repo.py`（9 个用例）走 `tests/conftest.py::async_session`，
  即共享 alembic 建出的 `public` schema。

**共享 `public` schema 在多 worker 下的实际语义**：每个用例开一个外层事务并在 teardown
`ROLLBACK`，`join_transaction_mode="create_savepoint"` 把用例内的 `session.commit()` 降级为
释放 SAVEPOINT，**数据始终不提交**。PG 默认 READ COMMITTED 下，未提交数据对其他连接不可见，
因此跨 worker 的读隔离天然成立。

**残余风险**（并行才会出现，串行不会）：

- 两个 worker 的用例向同一主键写入（如 `users.id = "default"` 这类固定测试数据）时，后者会**阻塞**
  等待前者事务结束，再互相等不同行即构成死锁——PG 会检测并杀掉其中一方，表现为随机失败。
- 任何绕过 `async_session`、自建 engine 并真提交的用例都会把数据泄漏给其他 worker。当前 92 个
  用例中不存在这种写法，但这是一条需要长期守住的隐式约束。

**实证**：Docker `postgres:16`，`alembic upgrade head` 后跑 `-m "uses_db and not sqlite_only"`，
`-n 4` 三轮、`-n 8` 一轮，**四轮均 92 passed**，未出现死锁、唯一约束冲突或锁等待超时。

**结论**：不建议并行——ROI 不成立（可省时间 < 3 分钟，而风险是偶发红）。若将来该 job 显著变长，
优先级由高到低的方案：

1. `--dist loadfile`：把 `test_agent_credential_repo.py` 的 9 个用例钉在同一 worker，其余文件本就
   per-test schema，零代码改动即可安全并行。
2. per-worker database（模板库克隆）：CI 里对模板库跑一次 alembic，再按 worker 数克隆。
   实测 `CREATE DATABASE ... TEMPLATE` 每库 36–110ms，成本可忽略：

   ```bash
   # 一次性：模板库跑 alembic
   DATABASE_URL=postgresql+asyncpg://arcreel:arcreel@localhost:5432/arcreel_tpl uv run alembic upgrade head
   # 每 worker 一个克隆库
   for i in 0 1 2 3; do
     psql -c "CREATE DATABASE arcreel_test_gw$i TEMPLATE arcreel_tpl"
   done
   ```

   `tests/conftest.py` 侧按 `PYTEST_XDIST_WORKER` 追加后缀（该环境变量由
   `xdist/remote.py:417-418` 写入，值形如 `gw0`）：

   ```python
   url = os.environ.get("DATABASE_URL", "")
   worker = os.environ.get("PYTEST_XDIST_WORKER")
   if url.startswith("postgresql") and worker:
       url = f"{url}_{worker}"
   ```

   代价是每 worker 一份 schema、CI 步骤变复杂，只在方案 1 不够用时才值得。

### 5. pytest-timeout 与 asyncio 用例的兼容方法（`thread` vs `signal`）

**来源**：pytest-timeout 源码 `DEFAULT_METHOD = "signal" if HAVE_SIGALRM else "thread"`；
官方文档对两者的描述是——`signal` 方法 POSIX-only、基于 SIGALRM、抛 `pytest.fail`，**会话继续**
且 fixture teardown 正常执行；`thread` 方法跨平台，超时后 dump 全部线程栈并 `os._exit(1)`
硬退出，**不做任何 pytest 清理，也不生成 XML 报告**。

**本仓库对照**：CI 全部跑在 `ubuntu-latest`（有 SIGALRM），开发机为 macOS / Linux，
`signal` 是默认值也是可用值。asyncio 用例并不特殊——SIGALRM 打断的是主线程，而 pytest-asyncio
的 `asyncio.run()` 正是在主线程上跑 loop，信号处理器抛出的异常会从 `run_until_complete` 里冒出来。

**实证**：`--timeout=3`

- `--timeout-method=signal`：`async def` 挂起用例与 `time.sleep(30)` 同步用例都被判为
  `Failed: Timeout (>3.0s) from pytest-timeout.`，两条都进 summary，会话正常收尾（`2 failed`）。
- `--timeout-method=thread`：第一条超时用例处进程即被杀，输出停在栈 dump，**第二条用例根本没跑**，
  也没有 summary 与退出摘要。
- `-n 2` + `signal`：两条都作为普通 failure 报出，唯一副作用是栈 dump 里混进 execnet 的接收线程栈
  （噪声，不影响判读）。

**结论**：采用 `signal`。定位是**挂死防护**而非性能闸——CI job 的 `timeout-minutes` 只能把整个 job
打红、无法指出是哪条用例挂住，`timeout` 能直接点名。阈值应远高于任何正常用例
（已测子集最慢用例 0.72s），建议 120s；确需更长的用例用 `@pytest.mark.timeout(600)` 单独放宽。

### 6. `_enforce_classification_markers` 在 xdist 下是否每 worker 重复执行、是否有 xdist 专用钩子

**机制**：xdist 的 controller 不收集用例，收集发生在**每个 worker 内**，controller 再比对各 worker
上报的 nodeid 集合。因此 `tests/conftest.py::pytest_collection_modifyitems`（及其调用的
`_enforce_classification_markers`）会在**每个 worker 各执行一次**。

**后果（实证）**：故意放一个缺分类 marker 的用例：

- 串行：输出正是设计意图——`ERROR: 1 个测试用例缺少分类 marker（unit/integration/e2e 三选一）：` 加
  逐条 nodeid 清单与修复指引。
- `-n 2`：worker 在收集期抛 `UsageError` 即崩，controller 撞上
  `xdist/dsession.py:217` 的 `assert not crashitem, (crashitem, node)`，输出退化为一大段
  `INTERNALERROR>` 栈，末行只有
  `AssertionError: ('tests/…::test_missing_classification_marker', <WorkerController gw1>)`，
  **原始中文清单一个字都不剩**，并以 `no tests ran` 收场。

**可用的 xdist 钩子**：`pytest_xdist_node_collection_finished(node, ids)` 在 controller 上、
worker 收集完成时触发，但它只拿得到 nodeid 字符串，**拿不到 marker**，无法就地复刻该校验。
`xdist.is_xdist_worker(request_or_config)` / `get_xdist_worker_id()` 可用于在 worker 内改变行为。

**关键实证（决定方案）**：`--collect-only` 下 xdist **不分发**，收集在 controller 本地完成，
校验因此照常触发且消息完整——串行与 `-n 4` 两种调用下均输出可读中文清单、退出码 **4**。
全量 `--collect-only` 只需 **1.92s**（10246 个用例）。

**结论**：需先改设施，但改动很轻——在 CI 并行步骤**之前**加一道 `--collect-only` 闸即可，
`conftest.py` 一行不用动。若还想让本地 `pytest -n 4` 也给出可读消息，可在
`_enforce_classification_markers` 里对 `is_xdist_worker(config)` 分支先把清单 `print` 到 stderr
再抛（xdist 会转发 worker 的 stderr），或在 worker 内降级为不抛、由 `--collect-only` 闸兜底。

---

## 建议的 `addopts` / CI 命令形态

### `pyproject.toml`

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--strict-markers --durations=25"
asyncio_mode = "auto"
timeout = 120
timeout_method = "signal"
```

- **不把 `-n` 写进 `addopts`**：并行只在大选集上有收益，而本地绝大多数调用是小选集，
  实测小选集并行更慢（见实证记录），且并行会打散 `-x` / `--pdb` 的定位体验
  （xdist 明确拒绝 `--pdb`，见 `xdist/plugin.py:333`）。并行只在 CI 命令里显式开。
- `--durations=25` 放进 `addopts` 是零成本的常开可观测项；判读时注意并行下数值被 CPU 争抢放大，
  只作相对排序。
- `timeout = 120` 是挂死防护阈值，不是性能门限。

### `.github/workflows/test.yml` — `backend-tests`

```yaml
      # 收集期闸：分类 marker 校验在 xdist worker 内抛 UsageError 会退化成 INTERNALERROR，
      # 消息全丢；--collect-only 由 controller 本地收集，消息完整，全量仅约 2s。
      - name: Marker hygiene (collection gate)
        run: uv run python -m pytest --collect-only -q

      - name: Run tests with coverage
        run: |
          uv run python -m pytest -m "not e2e" \
            -n 4 --dist loadfile \
            --cov=lib --cov=server \
            --cov-report=term-missing --cov-report=xml --cov-fail-under=80
```

- `-n 4` 而非 `-n auto`：`auto` 的取值依赖 psutil 是否恰好在依赖树里（`xdist/plugin.py:16-53`），
  今天没有、明天某个传递依赖带进来就会静默变成物理核数。要保留 `auto` 的弹性就同时设
  `PYTEST_XDIST_AUTO_NUM_WORKERS: "4"`。
- `--dist loadfile` 而非默认 `load`：同文件用例落同一 worker，直接消解
  `tests/test_app_data_dir.py` 的仓库根目录竞态与 module 级 fixture 的重复副作用，零代码改动。
  代价是负载均衡变差；若实测加速不足，再改用 `--dist load` 并给少数有文件级共享状态的文件打
  `@pytest.mark.xdist_group`。

### `.github/workflows/test.yml` — `postgres-compat`

**建议保持串行不变**：92 个用例、2m44s，可省时间小于共享 `public` schema 的并发风险。
若将来必须并行，先上 `--dist loadfile`（`test_agent_credential_repo.py` 的 9 个共享 schema 用例
即被钉在同一 worker），仍不够再上 §4 的模板库克隆方案。

### 落地前必须补的验证

本次实证未跑全量（约 10246 个用例），因此**全量加速比是外推值而非实测值**。落地 PR 必须先做：

1. 全量 `-n 4 --dist loadfile` 连跑 **3 轮**，确认没有 flaky——并行暴露的是用例间的隐式顺序依赖，
   单轮全绿不足以证明。
2. 同一 PR 里记录全量串行与并行的实际墙钟时间，据此决定 `-n` 取值与是否值得放宽
   `timeout-minutes`。
3. 确认 `--cov-fail-under=80` 在全量并行下仍通过（子集已验证合并准确，全量仍需一次确认）。

---

## 实证记录

环境：macOS（Darwin 25.5.0），8 逻辑核；插件为临时安装、实证后已 `git checkout` 还原
`pyproject.toml` 与 `uv.lock`。

### 加速比（本机空闲时紧邻测量，同一次调用内连续执行）

| 选集 | 用例数 | 串行 | `-n 2` | `-n 4` | `-n 4` 相对串行 |
| --- | ---: | ---: | ---: | ---: | --- |
| `tests/server tests/agent_runtime -m "not e2e"` | 1256 | 11.42s | — | 6.37s | **1.79×** |
| `tests/lib tests/integration tests/config tests/backends tests/source_loader -m "not e2e"` | 629 | 5.20s | 5.54s | 5.76s | 0.90×（更慢） |
| `tests/agent_session_store` | 45 | 1.81s | — | 3.56s | 0.51×（更慢） |

规律很干脆：**并行收益与选集规模正相关**。每个 worker 都要重新 import 一遍
`server.app` 等重模块，这笔固定开销在小选集上直接吃掉全部收益。全量 10246 个用例远大于
1256 这一档，因此按 1.79× 外推是保守的下界——但仍须按上文「落地前必须补的验证」实测。

> 先前几组数据（如同一 1256 用例选集测到 20.79s / 57.54s）采集时本机有其他负载，
> 波动达 5 倍，已全部作废；上表为本机空闲后重测。所有**通过/失败**类结论不受负载影响，
> **时长**类结论一律以上表为准。

### 覆盖率合并

`tests/agent_session_store` + `--cov=lib --cov=server --cov-report=term`：

| 模式 | 汇总行 |
| --- | --- |
| 串行 | `TOTAL 48340 35618 26%` |
| `-n 4` | `TOTAL 48340 35618 26%` |

逐字一致。附带：加 `--cov` 后该子集从 3.56s 涨到 38.46s，开销集中在报告生成（48340 行源码）
而非测量，与是否并行无关。

### PostgreSQL 并发

Docker `postgres:16`，`DATABASE_URL=postgresql+asyncpg://arcreel:arcreel@localhost:55432/arcreel_test`，
先 `alembic upgrade head`，再跑 `-m "uses_db and not sqlite_only"`（92 个用例）：

| 模式 | 结果 |
| --- | --- |
| 串行 | 92 passed |
| `-n 4`（三轮） | 92 passed × 3 |
| `-n 8`（一轮） | 92 passed |

四轮均无死锁、无唯一约束冲突、无锁等待超时。时长因本机当时有负载而不可比，故不列。

模板库克隆耗时（`CREATE DATABASE ... TEMPLATE arcreel_test`，四次）：110.4ms / 35.7ms / 45.2ms / 47.4ms。

### pytest-timeout

探针：一个 `async def` + `await asyncio.sleep(30)`，一个 `def` + `time.sleep(30)`；`--timeout=3`。

| 配置 | 结果 |
| --- | --- |
| `--timeout-method=signal`（串行） | 两条均 `Failed: Timeout (>3.0s) from pytest-timeout.`，`2 failed in 6.26s`，会话正常收尾 |
| `--timeout-method=thread`（串行） | 第一条超时处进程被杀，无 summary，第二条未执行 |
| `--timeout-method=signal` + `-n 2` | 两条均作为普通 failure 报出；栈 dump 中混入 execnet 接收线程栈 |

### 分类 marker 收集期校验

探针：一个不带 `unit`/`integration`/`e2e` 的用例。

| 调用 | 结果 |
| --- | --- |
| 串行 run | `ERROR: 1 个测试用例缺少分类 marker（unit/integration/e2e 三选一）：` + nodeid 清单 + 修复指引 |
| `-n 2` run | `INTERNALERROR>` 长栈，末行 `AssertionError: ('tests/…::test_missing_classification_marker', <WorkerController gw1>)`，原始消息全丢，`no tests ran` |
| `--collect-only`（串行） | 消息完整，退出码 4 |
| `--collect-only -n 4` | 消息完整，退出码 4（xdist 在 collect-only 下不分发） |
| 全量 `--collect-only` 耗时 | 10246 个用例，1.92s |

### CI 现状基线

CI run 32223755010（`.github/workflows/test.yml`）：

| job | 耗时 | `timeout-minutes` |
| --- | ---: | ---: |
| `backend-tests` | 20m22s | 25 |
| `postgres-compat` | 2m44s | 25 |
| `backend-static` | 1m03s | 15 |

---

## 参考来源

**官方文档**

- pytest-xdist — `docs/distribution.md`：`--dist load` / `loadfile` / `loadscope` / `loadgroup`
  的分组语义；`pytest --collect-only -n 4 --dist=load` 用于校验各 worker 收集一致。
- pytest-xdist — `_autodocs/006-hooks.md`：`pytest_configure_node`、`pytest_testnodeready`、
  `pytest_handlecrashitem`；CHANGELOG 记载 `pytest_xdist_node_collection_finished(node, ids)`。
- pytest-xdist — `_autodocs/002-dsession.md`：`is_xdist_worker()` / `get_xdist_worker_id()`。
- pytest-cov — README "Distributed testing"：xdist `load` 模式下 worker 各自测量、自动合并为单份报告；
  `--cov-append` 用于跨多次运行累加。
- pytest-timeout — `_autodocs/errors.md` 与 `_autodocs/types.md`：`signal` 抛 `pytest.fail`
  且允许 teardown 与后续用例继续，`thread` 走 `os._exit(1)` 硬退出、不生成 XML 报告；
  `DEFAULT_METHOD = "signal" if HAVE_SIGALRM else "thread"`。
- pytest-asyncio — `docs/reference/configuration.md`、`docs/concepts.md`、
  `docs/how-to-guides/change_default_fixture_loop.md`：`asyncio_mode` 语义与
  `asyncio_default_fixture_loop_scope` / `loop_scope` 的作用域控制。

**已安装源码（pytest-xdist 3.8.0 / pytest-timeout 2.4.0）**

- `xdist/workermanage.py:338-341` — 每个 popen worker 分配 `<basetemp>/popen-gwN`。
- `xdist/remote.py:417-418` — 写入 `PYTEST_XDIST_WORKER` / `PYTEST_XDIST_WORKER_COUNT`。
- `xdist/plugin.py:16-53` — `pytest_xdist_auto_num_workers`：`PYTEST_XDIST_AUTO_NUM_WORKERS`
  → psutil 物理/逻辑核 → `sched_getaffinity` → `cpu_count`。
- `xdist/plugin.py:333` — `--pdb` 与分发互斥。
- `xdist/dsession.py:217` — `assert not crashitem, (crashitem, node)`，worker 收集期崩溃的落点。
- `_pytest/tmpdir.py` — `TempPathFactory` 本身不感知 worker，隔离完全来自 xdist 传入的 `basetemp`。

**本仓库设施**

- `pyproject.toml` — `[tool.pytest.ini_options]`（`addopts`、`asyncio_mode`、markers）、
  `[tool.coverage.run] concurrency = ["greenlet", "thread"]`。
- `tests/conftest.py` — autouse `_reset_app_data_dir_cache` / `_stub_sandbox_check` / `_profile_env`；
  `async_session` 的 SQLite in-memory 与 PG `NullPool` + 外层事务 + `create_savepoint`；
  `generation_queue` 对 `_QUEUE_INSTANCE` 的改写；`pytest_collection_modifyitems` 自动注入
  `uses_db`；`_enforce_classification_markers`。
- `tests/agent_session_store/conftest.py` — PG 下 per-test `test_<uuid12>` schema，
  `file_session_factory` 落 `tmp_path`。
- `tests/test_app_data_dir.py` — 在 `PROJECT_ROOT` 下创建并 `rmdir` 目录。
- `tests/test_auth_coverage.py` — module 级 autouse `_auth_env`。
- `.github/workflows/test.yml` — `backend-tests` 与 `postgres-compat` 的命令与 `timeout-minutes`。
- CI run 32223755010 — 各 job 实际耗时。
