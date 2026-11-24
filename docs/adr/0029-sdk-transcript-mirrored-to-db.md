---
status: accepted
---

# Agent 用 SDK 会话 ID 寻址，数据库保存恢复上下文

新会话在首条消息发送时启动。SDK 返回会话 ID 后才创建持久记录，内存中的临时身份随之替换。收到 SDK ID 前会话不在列表内，也不能按持久 ID 订阅或恢复。这避免维护应用 ID 到 SDK ID 的第二份映射，也不会持久化从未发言的空会话。

SDK transcript 逐条镜像到运行时数据库，默认即时写入，以额外写入开销换取进程崩溃后可恢复的上下文。默认使用 `ARCREEL_SDK_SESSION_STORE=db`；`off` 保留 SDK 本地文件模式，适用于单机开发。数据库模式支持 [消息改写](0058-session-branch-prefix-fork.md)。历史 SDK 文件记录由启动迁移导入。界面时间线始终读 [会话事件日志](0048-session-event-log-single-ui-read-source.md)。

闲置会话延迟回收 SDK 客户端和进程，需要时按 SDK ID 恢复。CLI 仍在运行或有待完成工作时继续保留，容量耗尽时拒绝新会话，避免终止在途轮次或子任务。会话状态由 [SessionActor](0028-session-actor-single-task-serialization.md) 的 CLI 状态契约确定。
