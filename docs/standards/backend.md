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

### 跨模块使用的符号取公开名

模块级 `_` 前缀函数被别的模块 import 时，改成公开名，不加豁免。下划线表示「只在本模块内使用」，被外部 import 的符号已不符合这层含义。

## 并发

生成任务与请求处理中有哪些取消与中断来源，见 `website/docs/dev/architecture.md`「取消与中断的来源」（`#cancellation-sources`）。

### await 之后重新核对状态前提，前提已过时就补写当前状态或跳过副作用

跨 await 的窗口里另一条路径可能已经改过同一份状态：idle 结算落库期间会话重新进入 running，受理失败后递增的 epoch 让已入队的 idle 被判过时，连接锁释放到等待者醒来之间锁已被摘除。await 返回后先复核 epoch、锁身份或 meta 状态，前提仍成立再写入。改状态机本身时前提同样会过时：放开某个状态下的入口（如 running 时也接受发送）后，该入口原有失败分支假定的状态已变，逐条复核；计数类不变量的每个读点都隔着 await，先列出全部读到该计数的异步时序，再定修法。

### 事件循环线程只做调度

`async def` 沿调用链会做文件 I/O、哈希、大块解析或等锁的同步代码，整体放进 `asyncio.to_thread`；锁已有异步版本的，直接改用异步版本。事件循环线程上的一次阻塞会让同进程的全部请求排队：媒体文件分块发送，一个视频要经过事件循环几十次，卡顿被成倍放大。ruff `ASYNC` 只查 `async def` 函数体里的直接调用，看不穿函数调用；整条调用链由测试中的 blockbuster 护栏（`tests/blocking_guard`）把关，lint 通过不等于没有阻塞。

放行条件：应用启动与关停路径上的一次性阻塞，登记进 `tests/blocking_guard/exemptions.toml` 并写明理由。

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

## Windows 兼容

主开发平台是 macOS / Linux，server（连同它依赖的 workspace 子包）同时要在 Windows 原生环境完成项目创建与基础流程。CI 只跑 Linux，下面这些问题在 Linux 上全部静默通过，只会在用户的 Windows 机器上出现。

### 不可信路径缺少 `O_NOFOLLOW` 时，打开后校验文件身份

`O_NOFOLLOW` 这类 POSIX 常量经 `getattr(os, "<常量名>", 0)` 取值（由 ruff TID251 强制），在 Windows 上取到 0，`os.open()` 不再拒绝符号链接。这时只能靠 `is_symlink()` 预检，可预检与随后的 `os.open()` 之间有 TOCTOU 窗口：路径在窗口内被换成指向数据根外的链接，就会越权读写，属于安全边界问题。

不可信路径（用户或 Agent 能影响其中目录项的路径）分两步校验。打开前逐级拒绝符号链接，并把路径约束在数据根内，因为 `os.lstat` 会跟随中间目录的链接；打开后再用 `os.fstat` 与 `os.lstat` 比对 `st_dev` / `st_ino`。参考 `server/tool_runtime.py` 的 `_resolve_business_file` 与 `_read_business_file`，以及 `lib/artifacts/artifact_manifest.py` 对父目录身份的校验。做不到时在该平台拒绝操作。

放行条件：路径由服务端自行生成，目录内不会出现外部写入的符号链接，例如 `lib/agent/profile_manifest.py` 的项目锁、服务端自己原子落盘的产物。这类路径只做预检即可。
