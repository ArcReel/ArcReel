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

### 线程里写入正式文件或创建临时产物时，用 `run_sync_transaction`

在请求或任务上下文里，把写入、替换、删除正式文件，或创建需要交还调用方清理的临时产物的同步代码放进线程时，调用 `lib/infra/async_thread.py` 的 `run_sync_transaction`。它让已开始的线程事务结束、结果确定后再传播取消。裸 `asyncio.to_thread` 被取消时，`await` 立即抛出 `CancelledError`，线程却继续运行，调用方的清理逻辑与线程并发执行，有两类后果：

- 临时产物泄漏：线程在调用方清理之后才创建的临时目录、zip 等产物没有人回收。
- 外部已成功、内部报失败：`finally` 的清理与线程里的 `replace` 竞争，文件已经落盘，调用方却按失败处理。

放行条件：只读文件，或产出可丢弃的中间结果时，用裸 `asyncio.to_thread`。
