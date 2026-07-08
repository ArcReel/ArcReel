---
status: accepted
---

# 会话事件日志：UI 时间线唯一读源，与 SDK transcript 职责分离

智能体对话的 UI 真相若分散在多个来源（SDK transcript、内存 buffer、流式预览）且各来源没有共享的消息身份，一致性就只能靠内容比对启发式缝合，消息丢失/重复与渲染不一致会随边界情形持续渗漏。决定新增**会话事件日志**（每会话单调 seq 的持久化独立表）作为 UI 时间线唯一读源：

- 条目在**写入点定型**——task 通知、中断、skill 调用、subagent 归属等语义识别只在入日志一处发生并持久化；skill 条目只记名与入参，注入全文不进日志。
- **用户消息由服务端先写日志分配身份再回显**，POST 返回权威条目，前端不渲染任何本地合成消息，重试靠请求侧幂等键。
- **SSE 推 entry 流**（SSE 事件的 `id` 字段即 seq，断线按 cursor 续传）；turn 结构组装（合并连续 assistant、tool_result 回填、task 就地更新）是前端投影层的纯函数。
- **流式预览（draft）是服务端内存态**，身份为消息的 `message_id`，消息完成时被同 `message_id` 的日志条目精确替换；不入日志，崩溃即丢（与 agent 记忆一致）。
- **subagent 消息全量收录**为带 parent_tool_use_id 的条目，前端按 parent 归组为可折叠子任务卡片，主时间线只显示卡片。
- 日志是 transcript 的**物化视图**：可从 transcript 重放重建，旧会话首次访问时懒生成；漂移的修复手段是重建，不做双向同步。
- **SDK transcript 职责限于 SDK resume**（agent 记忆），不得混入 UI 专有条目（会被 resume 喂回 agent）。

## 明确不采用

- **收敛去重逻辑而不统一消息身份**：内容比对的边界渗漏无法消除。
- **直接以 transcript 为 UI 日志**：写入时机由 SDK 控制，无法在用户消息受理时分配身份；UI 专有条目污染 resume；subagent 独立 subpath 与 UI 时序不同构。
- **新旧协议 feature flag 双轨**：前端须并行维护两套 store 逻辑。

## Consequences

- 新增 UI 事件类型必须在写入点定型；渲染端与读取端不做语义嗅探、不做去重——UI 一致性问题的修复手段是修正写入点定型逻辑或重放重建日志。
- 事件日志表与 transcript 表并存是有意双写：前者是 UI 读模型、后者是 agent 记忆，二者内容重叠但协议与时序不同构，合表即回到本 ADR 要消除的耦合。
- transcript 的 eager flush（`docs/adr/0029`）仅为崩溃后 SDK resume 不丢上下文服务；UI 重连快照由事件日志承担。
