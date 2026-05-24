---
status: proposed
---

# Cancel running 任务通过 cancelling 中间态收敛 race，单一状态转移路径

任务状态机此前仅允许 `queued → cancelled`（`task_repo.py:_mark_cancelled` 硬拒非 queued 状态），用户在 UI 上看不到取消 running 任务的入口；底层 worker 的 `_process_task` 也没有响应外部 cancel 的接缝。我们把 cancel 扩展到 running 任务时，要解决一个新出现的 race：cancel API 是「日常路径」，要求秒级响应（不能同步等 worker 走完 finally），但 worker 内部 asyncio.Task 被 `task.cancel()` 之后到真正抛出 `CancelledError` 再到 finally 跑完写 DB 之间存在亚秒到秒级的延迟——这段时间 DB 里这条任务的状态是什么？三个备选：(a) cancel API 直接把 DB 写成 `cancelled` 然后让 worker finally 自检「已是 cancelled 就不写」；(b) API 不动 DB，等 worker finally 自己写；(c) 引入 `cancelling` 中间态，API 把 DB 写成 cancelling 后异步 cancel 本地 task，worker finally **只能**从 cancelling 转 cancelled。

我们选 (c)。(a) 把「谁赢」的判断逻辑散在所有出口——worker `_process_task` 的 finally、`mark_task_succeeded`、`mark_task_failed`、timeout 路径、resume 失败路径都得加同样的「若已是 cancelled 则保留」分支，每加一个新的终态路径就漏一处；(b) 用户点了取消还要等 1–2 秒才在轮询里看到状态变化，违背「秒级响应」的产品定位。引入 `cancelling` 把 race 收敛到状态机：`running → cancelling` 由 cancel API 写、`cancelling → cancelled` 由 worker finally 写、其他终态（succeeded/failed）的转移**必须**先看当前不是 cancelling——这条「不能从 cancelling 跳到非 cancelled 终态」的约束由 Repository 层的 SQL `WHERE` 子句硬性兜底，任何新增的 finally 路径只要走 Repository 就自动遵守，无需在调用方写 if。状态枚举多一个值的代价远小于在多处维护「谁赢」逻辑的代价。

## Consequences

- 状态机扩展为 `queued → running → succeeded | failed | cancelling → cancelled`。`cancelling` 是唯一允许从外部（cancel API）主动写入的非终态、且只能由 worker finally 进一步转为 cancelled。i18n 三语（zh/en/vi）各加一个 `cancelling` 状态标签（建议 zh "正在取消…"）；前端 TaskHud 的状态标签 + 自动消失逻辑 + cancel 按钮可用性都需要识别这个新值。
- `cancel_task` Repository 接口的状态校验从「只能 queued」放宽为「queued 或 running」：queued 直接转 cancelled（与现有语义一致），running 转 cancelling 并触发 worker 内 in-process task 字典查找 + `asyncio.Task.cancel()`。
- worker `_process_task` 的 finally 在收到 `CancelledError` 或 timeout 后，调用 `mark_task_cancelled`/`mark_task_failed`/`mark_task_succeeded` 时**不需要**自己判断「是否被外部 cancel 过」——Repository SQL 的 `WHERE status=...` 兜底会让从 cancelling 出发的 succeeded/failed 写入静默失败（0 rows affected）。但**收尾代码必须检查 row count**：如果 `mark_task_succeeded`/`mark_task_failed` 返回 0 行（说明 race 中任务已被外部改成 cancelling），finally 必须显式再调一次 `mark_task_cancelled` 把任务从 cancelling 推到 cancelled，否则任务会永远卡在 cancelling。一句话协议：worker 写终态时遵守「0-rows 即转 cancelled」。
- cancel 信号通道用 worker 内存 `dict[task_id, asyncio.Task]`：ArcReel 的 GenerationWorker 始终与 server 主进程捆绑在同一个 uvicorn 进程（参见 `CONTEXT.md` 的 `worker` 术语条），无跨进程传 cancel 信号的需要。后续若架构变成多 worker（目前没有规划），需要在 DB 加 `cancel_requested` 标记 + worker 轮询，但本 ADR 不预留。
- 「沉没成本接受」语义：cancel 立刻 `task.cancel()` 抛 CancelledError，正在进行的供应商 API 调用被 httpx 中断（连接关闭），服务器端可能仍在生成，已发出的费用 ArcReel 不退也不追。UsageTracker 现有「调用一次记一次」的语义不变；用户在 UI 上点 cancel running 时应有费用语义提示（按钮上 ⚠️ + hover tooltip 说明），但 confirm 弹窗会破坏秒级响应不引入。
- 不要把 cancelling 收紧为「API 必须同步等到 cancelled 才返回」：那等同于退回到方案 (b)，违背秒级响应。
