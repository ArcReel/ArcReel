---
status: accepted
---

# Agent 会话按访问规则、装配与消息流划分职责

SessionManager 持有会话生命周期及其并发状态。访问规则、SDK 装配、订阅广播和消息序列化分别维护，调用方通过明确的边界使用它们。拆分前，不同层测试要替换同一批私有方法，会话和项目事件服务还重复维护订阅。

- [AgentAccessPolicy](../../server/agent_runtime/agent_access_policy.py) 持有静态访问规则。规则分别投影为内核沙箱配置和 SDK 工具的逐次裁决。Windows 命令白名单和 Bash 环境清理共用这份规则。平台分别需要静态配置与动态裁决，抽成形状相同的平台接口会引入空操作。
- [OptionsAssembler](../../server/agent_runtime/options_assembler.py) 收集会话启动需要的配置和凭证，可以访问数据库。规则判断与现场凭证收集因此能分别测试。
- [SseChannel](../../server/sse_channel.py) 负责订阅、广播、空闲唤醒和背压。初始快照与会话日志补读由各消费方负责。会话流传递订阅屏障、实时消息和心跳，内部溢出信号由流组件消化。

SDK 客户端归 SessionActor 独占，见 [ADR 0028](0028-session-actor-single-task-serialization.md)。供应商密钥在启动、Agent 启动环境和 Bash 命令三处的保护见 [ADR 0027](0027-provider-secrets-banned-from-process-env.md)。

## 订阅生命周期

调用方通过 `async with` 消费会话和项目事件流，由上下文管理器负责订阅和退出注销。裸异步生成器在调用方提前跳出循环时不会自动关闭。断线取消可能发生在发送阶段，无法传入等待下一事件的迭代链，因此消费方在空闲唤醒时检查断线并正常退出循环。

会话流先交付订阅已建立的屏障，调用方随后读 [持久事件日志](0048-session-event-log-single-ui-read-source.md)，并用序号过滤与实时消息重叠的条目。项目事件流先交付初始快照。任一流的订阅者追不上事件时，服务端结束对应流，由客户端重连补齐。
