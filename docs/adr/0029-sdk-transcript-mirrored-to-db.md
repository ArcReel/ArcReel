---
status: accepted
---

# SDK transcript 镜像入自有 DB，默认 eager flush；idle 会话从内存驱逐、可恢复不丢历史

SDK 自带的 jsonl transcript 绑定本机文件系统，与「运行时状态统一进单一 async ORM DB」（`docs/adr/0020`）的部署形态（多用户/PostgreSQL）不匹配。决定实现自定义 SessionStore 把 transcript 逐 entry 镜像写入 DB（`agent_session_entries` + 会话摘要表），由 `ARCREEL_SDK_SESSION_STORE` 控制（默认 `db`，`off` 回退 SDK jsonl），启动钩子一次性把本地历史 jsonl 迁移入库；flush 模式默认 eager——逐条写入数据库，以换取崩溃后 SDK resume 不丢上下文。transcript 只服务 SDK resume，UI 时间线的读源是会话事件日志（见 `docs/adr/0048`）。

## Consequences

- 闲置会话在延迟后（`agent_session_cleanup_delay_seconds`，默认 300 秒，另有定期巡检兜底）从内存驱逐（关闭 actor/SDK 子进程）以约束常驻内存。仍有后台工作的会话不驱逐（关闭子进程会连带杀掉在途子智能体，完成后的自主轮次也不会发生），容量不足时宁可拒绝新会话；后台工作结束后重新计时。判据与 SDK 判定 run 是否结束的口径一致：CLI 报告的会话状态不是 idle（子智能体存活、或其完成后还欠一轮时 CLI 持续报 running），或任务账本里还有在途子智能体，两者取并集；CLI 不报状态时只剩账本。同样不设兜底上限：CLI 不报 idle、子智能体的终态帧不到，会话就一直保留到进程重启。正因 transcript 已入库，驱逐不丢历史：再次访问时按 sdk_session_id 以 SDK resume 重建 actor 续聊。
- eager flush 有写放大；SDK 在慢 store 下会自行合并帧。`off` 模式只适合单机开发。
