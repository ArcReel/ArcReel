---
status: accepted
---

# SDK transcript 镜像入自有 DB，默认 eager flush；idle 会话从内存驱逐、可恢复不丢历史

SDK 自带的 jsonl transcript 绑定本机文件系统，与「运行时状态统一进单一 async ORM DB」（`docs/adr/0020`）的部署形态（多用户/PostgreSQL）不匹配。决定实现自定义 SessionStore 把 transcript 逐 entry 镜像写入 DB（`agent_session_entries` + 会话摘要表），由 `ARCREEL_SDK_SESSION_STORE` 控制（默认 `db`，`off` 回退 SDK jsonl），启动钩子一次性把本地历史 jsonl 迁移入库；flush 模式默认 eager——逐条写入数据库，以换取崩溃持久性与中途重连快照。

## Consequences

- 闲置会话在延迟后（`agent_session_cleanup_delay_seconds`，默认 300 秒，另有定期巡检兜底）从内存驱逐（关闭 actor/SDK 子进程）以约束常驻内存。正因 transcript 已入库，驱逐不丢历史：再次访问时按 sdk_session_id 以 SDK resume 重建 actor 续聊。
- eager flush 有写放大；SDK 在慢 store 下会自行合并帧。`off` 模式只适合单机开发。
