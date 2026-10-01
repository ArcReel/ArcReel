---
paths:
  - "tests/**"
  - "packages/*/tests/**"
  - "frontend/src/**/*.test.*"
  - "frontend/src/test/**"
  - "frontend/src/__mocks__/**"
---

# 测试

结构类规则（零断言、只断言替身、patch 私有符号、conftest 结构、同一 patch 目标散落多文件、`fakes` / `factories` 的符号使用面、文件命名与体量、同文件重复用例）由 `scripts/audit_tests.py --check` 强制，命中时它给出修复指引；这里只收它判不了的部分。

## 分档与位置

### 用例按它真实触达的依赖归档

| 档位 | 允许触达 |
|---|---|
| `unit` | 内存对象与 `tmp_path`；不碰真实 DB、子进程、网络 |
| `integration` | 真实 DB、文件系统、随包 ffmpeg 子进程；用到真实 DB 的用例（含全部 alembic 迁移测试）一律在此 |
| `e2e` | 真实外部服务（远程 API、大模型调用）；CI 默认跳过 |

目录是 `tests/unit|integration|e2e/<源码顶层包镜像>`，档位 marker 按路径自动注入。镜像是否正确没有机械校验：用例放错档位时，`-m unit` 的快速循环会慢下来或开始依赖环境。workspace 子包的测试在 `packages/<子包>/tests/`，按子包源码镜像、不分档位，且不 import 主仓的任何模块。alembic 迁移测试每个迁移脚本一个文件，放在 `tests/integration/lib/db/migrations/`，维持 SQLite。

### 一个测试文件对应一个被测对象

同一被测对象超出可读体量时按行为域拆分，子文件用语义化的主题后缀命名（如 `ShotDetail.drama.test.tsx`）。前端测试与源文件同级并放。

## 替身

### 替身按优先级取用，只替换仓库边界

真实对象（内存 SQLite、`tmp_path`）＞ `tests/fakes.py` 手写替身 ＞ 带 `spec` / `autospec` 的 Mock ＞ 裸 `MagicMock` / `AsyncMock`。Mock 只替换仓库边界：第三方 SDK、网络传输、子进程、文件系统、时钟。仓库内的依赖用真实实例；替身只出现在真实对象触发不了的分支（异常、超时、外部失败）。被替身包住的仓库内对象，它和被测对象之间的契约就不再被测试。

### 需要控制内部行为时加 seam，seam 是带生产默认值的显式参数

seam 是构造参数或关键字参数，带生产默认值，不改变生产行为，例如 `retry_async(operation, *, clock=..., jitter=...)`。适用于轮询时钟、间隔、退避、HTTP 探测客户端、文件系统与子进程。模块级可替换全局不算 seam：它让测试之间共享可变状态。可测性改造同样不得改变生产行为，前端只允许抽纯函数与 hook 级的结构性抽取。

### 视频能力的消费方测试直接构造视频请求事实

报价、预检、执行等消费方的测试，用 `tests/factories.py` 的 `make_video_request_facts` 构造结果对象或失败对象，不在能力解析器层造假，也不手搭能力 dict。能力求值本身用真实 `ConfigResolver` 加测试数据库测。

### 进程级缓存的重置钩子取公开名 `reset_*_for_tests()`

生产模块用 `functools.cache` 这类进程级缓存时，暴露公开的 `reset_*_for_tests()`（如 `lib.infra.app_data_dir.reset_for_tests`），只清缓存、不改生产行为。这是过渡形态：新代码优先把缓存依赖做成参数注入。

### 出站 HTTP 用 respx 拦截，FastAPI 依赖用 `dependency_overrides`

respx 保留真实 httpx 客户端、在 transport 层拦截（`AsyncOpenAI` 的流量同样被捕获），断言的是真实序列化后的请求。路由依赖用 `app.dependency_overrides` 替换，不 patch。

### 同一个替身目标在多个文件各自 monkeypatch，收编为共享 fixture

`patch` / `patch.object` 的散落由闸门判定；`monkeypatch.setattr` 同一目标出现在 3 个以上测试文件时同样说明缺一个共享 fixture 或 seam，review 时按同一标准要求收编。专题共享模块（如 `tests/auth_deps.py`、各目录的 `*_support.py`）的公开符号应被多个测试文件使用，只服务一个文件的 helper 放回该文件。

### 前端 API 打桩边界是 `vi.spyOn(API, method)`

新增出站调用一律经 `API` class，测试在这一层 spy；`api.ts` 本体的测试用手写 fetch / Response stub；不引入 msw。SSE 统一用 `src/test/` 的 `FakeSseStream`（由 `API.openProjectEventStream` / `API.openAssistantEntriesStream` 的 spy 返回），流式客户端 `openSseStream` 与 `api.ts` 的流式封装用 `src/test/fakeSseFetch.ts`。

### 前端只 mock 三类内部子组件

重量级（虚拟化、动画、canvas）、有副作用（发请求、启动定时器）、与本测试无关的纯展示。mock 掉承载被测交互的子组件，测试就只剩渲染外壳。同一组件被 3 个以上文件 mock 时上提到 `src/__mocks__/`；与被测对象无关的横切工具（`createDeferred`、`FakeSseStream`、factories）与本地 `renderXxx` 同理，同一形状重复出现在 3 个以上文件时上提到 `src/test/`。

## 断言价值

### 每条用例保护一个可观察的契约

断言落在真实产出上：返回值的类型与属性、respx 捕获的真实请求、可观察状态。闸门之外，下面四种形态由 review 判断：

- **重复弱化**：与另一条用例覆盖同一路径，断言却是它的真子集。
- **过度表征**：断言日志文本、字典键序、私有属性等实现细节，而不是契约。
- **setup 与断言严重失衡**：几十行准备，最后只断言一个调用发生过。
- **断言辅助函数里只断言替身调用记录**：把闸门判据藏进了 helper。

命中后的处置固定三步：① 行为已有其他用例实质覆盖 → 删除，不做覆盖补偿；② 无覆盖但行为不值得保护（没有真实分支或契约约束）→ 删除；③ 值得保护 → 改造，需要在生产代码加 seam 的转入 seam 收编批次。无意义测试是负价值，删除优于保留。

### 覆盖率与变异得分只是信号

不为覆盖率数字或杀死 mutant 写测试；删除无意义测试允许覆盖率下降。变异测试的批次流程与验收见 `docs/testing/mutmut-runbook.md`。

## 时序

### 等待、重试、超时经时钟 seam 或事件握手驱动

真实时间等待（`time.sleep`、固定 `setTimeout`）让用例变慢，并在负载高时偶发失败。偶发失败视同普通缺陷，就地改成时钟 seam 或事件握手；修不了或不值得修的按上面的三步处置删除。不引入自动重试（pytest-rerunfailures、CI job 级 retry），它会掩盖本应暴露的失败。前端用例沿用 vitest 默认 5s 超时，个别慢用例显式覆写并写明原因。

放行条件：概率性 stress 用例（真实并发 + 真实时间）在这里登记后才允许存在。当前唯一登记的是 `tests/integration/lib/project/test_project_manager_concurrent_save.py` 的原子写压力用例。
