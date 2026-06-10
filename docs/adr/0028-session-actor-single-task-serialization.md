---
status: accepted
---

# SessionActor：每会话单 asyncio task 串行化全部 ClaudeSDKClient 调用

ClaudeSDKClient 不能安全并发调用，且其内部运行在 anyio 上，跨 task 持锁调用易死锁、破坏 SDK 内部状态机假设。决定每个 agent 会话配置一个专属 actor task，独占该会话的 ClaudeSDKClient：query/interrupt/disconnect 全部经 command queue 投递、在 actor task 内串行执行，流式接收消息期间用 `asyncio.wait` 与命令队列交错（中断不必等待整轮流式结束）；对外只通过 `on_message` 回调推送消息。否决了「调用方各自加锁」与「直接并发调用」。

## Consequences

- 任何新增的 SDK 操作必须走 command queue 进 actor，不得在外部 task 直接操作 client。
- actor 是会话常驻内存的主体，生命周期（驱逐/巡检/恢复）由 SessionManager 管理（见 `docs/adr/0029`）。
