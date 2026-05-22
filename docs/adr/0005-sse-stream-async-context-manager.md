# 会话/项目事件 SSE 流暴露为 async 上下文管理器，消费方在空闲哨兵上轮询 is_disconnected 兜清理

会话(`SessionManager`)与项目事件(`ProjectEventService`)的实时推送，过去把裸 `asyncio.Queue` 当接口暴露：每个消费方自己 `subscribe` → 手写 `while queue.get()` 循环 → `finally: unsubscribe`。漏写 `finally` 即订阅者泄漏。我们把它收敛为一个 **async 上下文管理器** `stream_messages()` / `stream_events()`，`subscribe/unsubscribe` 收为私有，订阅 + 回放 + 队列消费 + 退出注销都藏在接缝背后。但**仅把它做成 async generator 并不能保证清理**：`async for ... break`(以及客户端断线)**不会**自动 aclose 一个 async generator，其 `finally` 要等垃圾回收才由事件循环补跑——不确定、不及时。FastAPI 0.136 的原生 SSE(`fastapi.sse`)用结构化并发收尾(PEP-789：把端点生成器交给请求级 `AsyncExitStack` 上的 task group，断线时 `cancel_scope.cancel()` 而非抛 `GeneratorExit`)，取消信号只在生成器**正等下一条**时才会顺 `__anext__` 链传进嵌套生成器的 `finally`；若生成器停在自己的 `yield`(消费端不取、缓冲满——断线常见情形)，取消打在 send 上，**够不到**嵌套 `finally`。因此我们决定:(1) 接口形态用 **async 上下文管理器**而非裸 generator，让 `__aexit__` 承载清理;(2) 消费循环用一个 `_idle` 空闲哨兵定期醒来、`await request.is_disconnected()` **主动发现断线并正常 break**，使 `__aexit__` 确定性触发，不赌取消注入能否准点击中。`project_events.py` 早已是这个 `is_disconnected` 轮询路子——本决策把会话 SSE 路径对齐过去。

## Consequences

- `stream_messages()`/`stream_events()` 必须以 `async with` 消费，不能当裸 generator 直接 `async for`。新增 SSE 消费方一律走 CM 形态;评审遇到裸 `async for some_stream()` 即视为清理隐患。
- 迭代器吐"控制信号"沿用 in-band 哨兵(`{"type": "_idle"}` / `_replay_done` / `_queue_overflow`)，与历史上已存在的 `_queue_overflow` 一致;消费方靠 `msg.get("type")` 分发。**不要**为此另起 typed event 体系或带外通道——会偏离既有约定且无收益。
- 空闲哨兵一物两用:SSE 路径在其上查 `is_disconnected` + 发心跳;非 SSE 的同步收集方(`agent_chat._collect_reply`，无 `request`)在其上查自己的 deadline/会话状态。同一个哨兵，各接各的存活策略。
- 不要把"客户端断线时的及时清理"单独寄望于 FastAPI/anyio 的取消注入或 GC。断线及时性由消费方的 `is_disconnected` 自检负责;CM 的 `__aexit__` 负责"无论因何退出都注销"。两者缺一:只有 CM 而不自检，parked-at-send 断线仍漂到 GC;只自检而无 CM，异常路径仍可能漏注销。
- `ProjectEventService` 的"队列满则静默丢订阅者"语义保持不变(无 `_queue_overflow` 哨兵);本次只收编接口形态，不改其溢出行为。被静默丢弃的订阅者最终由 `is_disconnected` 自检或对端重连收场,这一既有近似不在本决策范围内。
