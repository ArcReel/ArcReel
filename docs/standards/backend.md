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

### 标注表达期望，运行期判定交给 `schema_guards`

从磁盘 JSON 重建的数据类，构造期的形状校验走 `lib/infra/schema_guards.py`，谓词以 `object` 收参。在已标注 `dict[str, Any]` 的参数上再写 `isinstance(x, dict)`，是对自身标注的同义反复：basedpyright 会判它恒真（`reportUnnecessaryIsInstance`），真正的磁盘脏数据反而没人校验。只校验外层容器类型的访问器，元素标注写成 `Any`，不写成 `dict[str, Any]`——后者承诺了没有校验过的形状。

### 穷尽分支后的兜底写 `assert_never(x)`，死分支删除

`reportUnreachable` 不用忽略注释绕过。防御性兜底写成 `assert_never(x)`：新增枚举成员时类型检查会在这里报错，而不是运行期静默落进兜底。确实走不到的分支直接删除。

### 回调里产出、外层消费的结果用单元素列表当信箱

basedpyright 不跟踪回调里的赋值，`x: T | None = None` + `nonlocal` 会让外层的空值判定被当成恒真，随后的分支被判不可达。用 `box: list[T] = []` 在回调里 `append`，外层读 `box[0]`。

### 跨模块使用的符号取公开名

模块级 `_` 前缀函数被别的模块 import 时，改成公开名，不加豁免；模块级 pytest fixture 同样取公开名（由 pytest 按名收集，无人 import，带下划线会被判未使用）。下划线表达「只在本模块内用」，被外部 import 的符号已经不是。

放行条件：装饰器就地注册的处理器（`@app.exception_handler`、`@router.*`、`@server.tool`、`@event.listens_for`）定义在函数作用域内，`reportUnusedFunction` 把它们误判为未使用。逐个挂 `# pyright: ignore[reportUnusedFunction]`，理由在注册块开头写一条注释，不在每行重复。

## 依赖边界

### 新增 import-linter ignore 之前，先尝试消除这条依赖边

分层契约是 `lib.config < lib.backends.*_backends < lib.custom_provider < lib.market`，子包 `arcreel_market_core` 不依赖主仓。ignore 条目让契约在这一处失效，之后同方向的依赖会顺着它继续长。只有依赖边确实无法消除时才加，并按 `pyproject.toml` 中的约定写明原因。

### 只在 `fastapi` / `pydantic` 没有 re-export 时直接依赖底层包

直接 import `starlette` / `pydantic_core` 会把底层包的版本绑进我们的依赖面；上层已经 re-export 的符号从上层取。确实需要底层包时，在 `pyproject.toml` 中显式声明它。
