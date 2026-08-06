# 完成契约

在批量生成、批量修改或任何可能部分失败的操作中，用本契约判定完成。

## 1. 固定请求集合

调用前记录：

- `requested_ids`：本次承诺处理的全部 ID；
- `before_revision`：workflow status 的项目与目标 revision；
- `expected_effect`：每个 ID 应变为 current、被删除、被插入或被确认。

省略 ID 的“补齐”调用，以调用前 status 返回的 `missing_ids + stale_ids` 作为 `requested_ids`。空集合表示无工作，不表示错误。

## 2. 执行并保留逐项结果

工具结果按 ID 归为：

- `succeeded`：工具完成且持久化结果可验证；
- `failed`：工具明确报告失败；
- `blocked`：审核门、配置、依赖或用户确认阻止执行；
- `unaccounted`：工具没有解释的请求 ID。

依赖任务在前置失败后停止；彼此独立的任务可继续，但最终状态必须逐项报告。

## 3. 重新验证

状态改变后重新调用 `get_workflow_status`，并在需要时读取对应正式文件。成功项同时满足：

1. workflow status 将该 ID 标为 `current`；
2. 持久化字段存在且指向项目内普通文件；
3. 文件存在；
4. provenance 的 `input_hash` 与当前输入一致；
5. 工具返回与持久化结果相符。

“任务已入队”“字段非空”或“旧文件仍存在”都不是充分完成条件。

## 4. 穷尽等式

完成时必须成立：

```text
requested_ids = succeeded ∪ failed ∪ blocked
succeeded、failed、blocked 两两不相交
unaccounted = ∅
```

不满足时状态为 `PARTIAL`，列出所有未解释 ID。

## 5. 返回状态

- `DONE`：全部 requested ID current；
- `DONE_WITH_CONCERNS`：全部 current，但工具或审核产生非阻塞告警；
- `PARTIAL`：成功与失败/阻塞并存，或存在未解释 ID；
- `BLOCKED`：未发生目标写入，原因可操作；
- `NEEDS_CONFIRMATION`：工具返回明确确认清单，尚未执行付费或破坏性动作。

摘要先给状态，再给 current / failed / blocked ID，最后给验证后的下一动作。
