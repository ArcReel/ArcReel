---
status: accepted
---

# SessionActor：每会话单 asyncio task 串行化全部 ClaudeSDKClient 调用，消息调度交给 CLI

ClaudeSDKClient 不能安全并发调用，且其内部运行在 anyio 上，跨 task 持锁调用易死锁、破坏 SDK 内部状态机假设。决定每个 agent 会话配置一个专属 actor task，独占该会话的 ClaudeSDKClient：query/interrupt/撤回排队消息/disconnect 全部经 command queue 投递、在 actor task 内串行执行；消息流在会话存续期间持续读取，用 `asyncio.wait` 与命令队列交错（中断不必等待整轮流式结束）；消息流终止（CLI 退出）时 actor 随之退出，由会话层按终态收尾；对外只通过 `on_message` 回调推送消息。否决了「调用方各自加锁」与「直接并发调用」。

消息调度只有 CLI 一层。CLI 自带消息队列：轮次进行中收到的用户消息在工具边界并入当前轮，或在本轮结束后与其他排队消息合并为新一轮；后台任务的完成通知走同一个队列，并会不经 query 开启自主轮次。因此：

- **用户消息一律立即交给 CLI**，不按「有轮次在跑」拦截或暂存；每条消息带服务端分配的 uuid，「立即发送」以 `now` 优先级送入，由 CLI 打断当前轮先处理。
- **actor 不撮合 query 与 result**：result 只代表一轮结束，不对应任何一条 query。
- **会话状态以 CLI 报告的 session state 为准**：消息送达即为 running，CLI 报 `idle`（队列排空、后台等待结束后才发出）或 actor 退出时才离开 running；result 不切换状态。
- **排队消息的去向以 CLI 的 `command_lifecycle` 帧为准**（`started` 即被接纳，`cancelled` 即被丢弃）。Python SDK 的消息解析会丢弃这类帧，actor 因此直接读 `client._query.receive_messages()` 的原始帧，自行处理 `command_lifecycle`，其余帧交给 SDK 的 `parse_message`。

## Considered Options

- **ArcReel 在 CLI 之上再叠一层调度**（一条用户消息对应一轮、对应一个 result；有轮次在跑时拒收新消息；按 query/result 切换状态、清空回显登记）：CLI 不保证这种一一对应，排队消息被并入当前轮或合并为一轮、自主轮次插在中间时，result 会被算给错误的 query，回显登记被提前清空导致重复落库，状态被覆写。两层调度对不上，竞态只能逐个打补丁。

## Consequences

- 任何新增的 SDK 操作必须走 command queue 进 actor，不得在外部 task 直接操作 client。
- actor 是会话常驻内存的主体，生命周期（驱逐/巡检/恢复）由 SessionManager 管理（见 `docs/adr/0029`）。
- actor 依赖 SDK 的三个私有入口：`client._query.receive_messages()`、`parse_message` 与 `_query._send_control_request`（撤回排队消息用的 `cancel_async_message`，SDK 未公开）。升级 SDK 时由测试锁住这三处。
