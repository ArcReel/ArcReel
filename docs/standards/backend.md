---
paths:
  - "lib/**"
  - "server/**"
  - "scripts/**"
  - "alembic/**"
  - "packages/**"
---

# 后端 Python

## 类型

### 从磁盘 JSON 重建的数据，形状校验走 `lib/infra/schema_guards.py`，谓词以 `object` 收参

类型标注表达期望，运行期判定由 `schema_guards` 负责。在已标注 `dict[str, Any]` 的参数上再写 `isinstance(x, dict)`，是对自身标注的同义反复：basedpyright 会判它恒真（`reportUnnecessaryIsInstance`），真正的磁盘脏数据反而没有被校验。只校验外层容器类型的访问器，元素标注写成 `Any`，不写成 `dict[str, Any]`，因为后者承诺了没有校验过的形状。

### 穷尽分支后的默认分支写 `assert_never(x)`，走不到的分支删除

新增枚举成员时，类型检查会在 `assert_never(x)` 处报错，而不是让新成员在运行期静默落进默认分支。确实走不到的分支直接删除。

### 回调里产出、外层消费的结果用单元素列表当信箱

basedpyright 不跟踪回调里的赋值，`x: T | None = None` 加 `nonlocal` 会让外层的空值判定被当成恒真，随后的分支被判不可达。用 `box: list[T] = []` 在回调里 `append`，外层读 `box[0]`。

### 跨模块使用的符号取公开名

模块级 `_` 前缀函数被别的模块 import 时，改成公开名，不加豁免。下划线表示「只在本模块内使用」，被外部 import 的符号已不符合这层含义。

## 依赖边界

### 新增 import-linter ignore 之前，先尝试消除对应的依赖边

分层契约是 `lib.config < lib.backends.*_backends < lib.custom_provider < lib.market`，子包 `arcreel_market_core` 不依赖主仓。ignore 条目让契约在这一处失效，之后同方向的依赖会沿着它继续增加。只有依赖边确实无法消除时才添加，并按 `pyproject.toml` 中的约定写明原因。

### 只在 `fastapi` / `pydantic` 没有 re-export 时直接依赖底层包

直接 import `starlette` / `pydantic_core` 会把底层包的版本绑进本仓库的依赖面；上层已经 re-export 的符号从上层导入。

## 并发

### await 之后重新核对状态前提，前提已过时就补写当前状态或跳过副作用

跨 await 的窗口里另一条路径可能已经改过同一份状态：idle 结算落库期间会话重新进入 running，受理失败后递增的 epoch 让已入队的 idle 被判过时，连接锁释放到等待者醒来之间锁已被摘除。await 返回后先复核 epoch、锁身份或 meta 状态，前提仍成立再写入。改状态机本身时前提同样会过时：放开某个状态下的入口（如 running 时也接受发送）后，该入口原有失败分支假定的状态已变，逐条复核；计数类不变量的每个读点都隔着 await，先列出全部读到该计数的异步时序，再定修法。

### 事件循环线程只做调度

`async def` 沿调用链会做文件 I/O、哈希、大块解析或等锁的同步代码，整体放进 `asyncio.to_thread`；锁已有异步版本的，直接改用异步版本。事件循环线程上的一次阻塞会让同进程的全部请求排队：媒体文件分块发送，一个视频要经过事件循环几十次，卡顿被成倍放大。ruff `ASYNC` 只查 `async def` 函数体里的直接调用，看不穿函数调用；整条调用链由测试中的 blockbuster 护栏（`tests/blocking_guard`）把关，lint 通过不等于没有阻塞。

放行条件：应用启动与关停路径上的一次性阻塞，登记进 `tests/blocking_guard/exemptions.toml` 并写明理由。

### 按真实的取消来源设防

生成任务与非流式请求处理中，已核实的打断来源有两类：任务内部的超时到期，`asyncio.timeout` 块取消当前任务，`asyncio.wait_for` 取消它等待的协程，两者都向外转成 `TimeoutError`；关停期间再次 Ctrl-C 强制退出，事件循环拆除时取消全部任务。以下三件事不打断执行中的协程：正常关停由 `GenerationWorker.stop()` 等在飞任务跑完，用户取消只对排队任务生效（`GLOSSARY.md` 「取消」），客户端断开只置标志位。

在这一范围里，下面两条规则防的是超时与将来新增的 `cancel()`。事件循环拆除不设防，被它打断的生成任务由 `GenerationWorker` 的取消分支标成 `interrupted`，来不及标记的由重启恢复收尾。

这一范围之外，不少场景自己发起取消，例如 SSE 流式响应在客户端断开时取消事件生产协程，`TrialRunManager.cancel()` 取消进行中的供应商试跑，`SessionManager` 在会话驱逐或启动失败时取消收件处理任务。在这些场景里，先从代码找出全部取消来源，再核对下面两条规则。

### 线程里写入正式文件或创建临时产物时，用 `run_sync_transaction`

在请求或任务上下文里，把写入、替换、删除正式文件，或创建需要交还调用方清理的临时产物的同步代码放进线程时，调用 `lib/infra/async_thread.py` 的 `run_sync_transaction`。它让已开始的线程事务结束、结果确定后再传播取消。裸 `asyncio.to_thread` 被取消时，`await` 立即抛出 `CancelledError`，线程却继续运行，调用方的清理逻辑与线程并发执行，有两类后果：

- 临时产物泄漏：线程在调用方清理之后才创建的临时目录、zip 等产物没有人回收。
- 外部已成功、内部报失败：`finally` 的清理与线程里的 `replace` 竞争，文件已经落盘，调用方却按失败处理。

线程产出的产物能在线程内用完的，在线程内创建并删除。必须交回异步侧的，线程里一创建就登记进调用方持有的列表，调用方在 `finally` 按列表回收：`run_sync_transaction` 被取消后等线程结束再抛出取消，返回值随之丢弃，登记是调用方看到产物的唯一途径。

放行条件：只读文件，或产出可丢弃的中间结果时，用裸 `asyncio.to_thread`。

### 供应商返回之后的落账段不可中断

`GLOSSARY.md` 「取消」要求已发出的供应商调用照常跑完、结果照常成为产物。落到代码上：从供应商返回或产物提交起，到执行器返回前，这一段里的每个线程调用用 `lib/infra/async_thread.py` 的 `run_noninterruptible_sync`，它等线程结束后把结果交回调用方，本次取消随之消失。两种已出现过的事故形态：参考图压缩副本的清理与线程并发，把刚付费拿到的图片一起删掉；完成回调与失败补偿同时执行。

它吞掉取消，调用点放在 `asyncio.timeout` 块与 `asyncio.wait_for` 之外，否则超时失效。

这条规则只护住段内的线程调用。段内的异步写入只保证写入本身不被中断，取消照常向外传播，其后的步骤随之跳过：`Ledger.record()` 的结算写入被取消后等写入落地再重新抛出，产物提交就不再执行；裸 `asyncio.shield` 被取消时立即抛出，写入与调用方的后续处理并发执行。生成链路的超时块都在供应商调用一侧，眼下只有事件循环拆除会打进这一段。给执行中的任务新增 `cancel()` 之前，先用 `run_noninterruptible_async` 把整段收进不可中断的异步边界。

### 派生结果在输入未变时复用

认领、请求、轮询这类高频路径上的现状重算，先比对输入的廉价标识（文件 stat、修订号），输入未变就复用上次的结果。按现状重算保证正确性，代价却随调用频率和输入规模相乘：每个认领周期、每个请求都重新哈希同一批文件，开销就随排队任务数线性增长。
