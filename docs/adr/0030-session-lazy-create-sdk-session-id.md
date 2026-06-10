---
status: accepted
---

# 会话发送即创建，对外身份统一为 sdk_session_id

预建会话行需要自行生成 ID，再在 SDK 返回 session_id 后维护一张映射，且会产生「已创建但从未发言」的孤儿行。决定不预建：新会话先以临时内存态启动 actor 并发送首条消息，待 SDK init 消息返回 session_id 后才写入 DB 会话行，并把内存 key 从临时 ID 替换为真实 ID（key swap）；此后查询/更新/恢复一律以 `sdk_session_id`（UNIQUE）为对外唯一身份。

## Consequences

- 首条消息的 SDK init 返回前，会话不可寻址（不在列表、不能按 ID 订阅/恢复）。
- key swap 是敏感路径：任何在「已发送、init 未回」窗口内按 ID 找会话的代码都要考虑临时 ID 阶段。
