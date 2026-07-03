---
status: accepted
---

# SessionManager 拆解边界：AgentAccessPolicy 双投影、装配器 I/O 对称、typed 事件流与 SseChannel

`server/agent_runtime` 的 SessionManager（约 2900 行、近百方法）把六种互不相干的职责物理塞进一个类：访问与沙箱规则、SDK options 装配、订阅广播、token/cost 抽取、会话生命周期、消息序列化。回放边界状态机泄漏在消费方 AssistantService 内（解码 `_replay_done` / `_idle` / `_queue_overflow` 哨兵），访问规则只能 monkeypatch 私有方法测试，订阅广播与项目事件服务重复实现。决定按职责析出 interface 小、implementation 深的独立 module，边界如下：**AgentAccessPolicy** 单一类承载「agent 能碰什么」的规则真相源，同一份规则做两种投影——为内核沙箱层编译 settings、为应用层 hook 提供逐次读/写/命令裁决；零 I/O、以进程级根路径纯构造；Windows 降级（Bash 前缀白名单）以 `sandbox_enabled` 构造参数收口在类内；Bash 密钥剥离归入本类（包装与白名单匹配存在互斥耦合，须局部化）；只输出纯裁决，SDK 封装与权限链顺序留在会话管理侧薄 adapter（SDK 是可选依赖，其缺失判断仅存在于 adapter）。命名不用 SandboxPolicy——词汇表规定「agent 沙箱」专指内核级隔离层，本 module 同时服务不属于沙箱的应用层 hook。**Options 装配器**为持依赖、允许 I/O 的类，凭证注入（DB → SDK 子进程 env，传输而非配置遗留）由其负责；与 AgentAccessPolicy 的 I/O 决策遵循同一判据——规则静态则零 I/O，装配天职是开会话时现场收集则允许 I/O。**typed 事件流**：SessionManager 对 AssistantService 的消息流保持单一 async 上下文管理器，产出改为语义化事件（回放批次一次交付 → 逐条直播 → 心跳 → 溢出即流终、流终即重连信号），回放与订阅的原子衔接收回 seam 内；此决策重新评估 `docs/adr/0005` 的「不要另起 typed event 体系」禁令——该禁令的语境是 seam 不动、消费方解码哨兵，seam 重画后 typed 事件是哨兵收编的自然形态，0005 的核心（CM 形态、`is_disconnected` 自检、`__aexit__` 兜清理）原样保留，其哨兵消费表述随实现修订。**SseChannel** 收敛会话流与项目事件流的订阅广播：职责限于订阅/退订、广播、空闲心跳、溢出处理（策略参数化：逐非关键消息+溢出信号 vs 移除订阅者），首/末订阅者生命周期钩子可选；开场白（缓冲回放 / 初始快照）不进组件，原子性由消费方持锁保证；已废弃的任务流端点（数据库轮询式）不接入。token/cost 抽取与消息序列化析出为纯函数 module；会话生命周期、容量驱逐、巡逻互相咬合、共享会话表与锁，留在 SessionManager 核心；SessionActor 一字不动（`docs/adr/0028`）。除移除供应商 base_url 的环境变量兜底外，拆解为纯结构重组，行为语义逐字保留。

## 明确不采用

- **Windows 降级拆为平台 adapter**（架构评审报告倾向）：被否决。两平台的产出形状不对称——内核路线产出一份启动配置（运行时对 Bash 撒手），降级路线产出运行时逐条判定——强套同一 adapter interface 得到两边各有半数空操作的抽象；而真正共享的路径规则两平台完全一致，拆分后还需第三个共享核承载它。平台差异用一个布尔分支表达比两个半空 adapter 诚实。未来评审再提 adapter 拆分时以此为准。
- **AgentAccessPolicy 做成纯函数集**：每个函数需接收 5+ 个根路径参数，签名爆炸且调用方仍要自己攒参数——复杂度只是从类内挪到调用点。
- **SseChannel 包含开场白**（订阅时回调「开场白生产函数」）：缓冲回放与现场扫描快照无一行共同实现，参数化即假抽象——组件没有变深，只多一层转发。
- **流接口拆成两段调用**（先取回放列表、再订阅直播）：两次调用之间漏消息；单流设计的动机正是回放与订阅在同一把锁内无缝衔接，堵缝需跨调用传游标或共享锁，接口反而更复杂。
- **凭证注入并入 AgentAccessPolicy**（按「同属密钥卫生」归类）：按 ADR 主题归类不如按依赖形状归类——注入读 DB、剥离零 I/O，塞进同一类即破坏后者纯构造可测的根基。

## Consequences

- AssistantService 不再感知任何哨兵；in-band 哨兵降为 SseChannel 内部机制。`docs/adr/0005` 在 typed 事件流落地时同步修订（表述现状，不留补丁式取代链）。
- 访问规则测试从 monkeypatch 私有方法/环境变量转为构造参数喂入断言 allow/deny；迁移期断言逐字保留，冗余清理独立后置。
- 供应商密钥的环境变量围堵（启动断言、空值覆盖、Bash 剥离）定性为常驻机制——「传输」（SDK 子进程只认 env 认证）与「围堵」（父进程环境是外部输入）两个成因都不随 DB 化配置消失，不得当技术债清理。
- CONTEXT.md 随实现新增 AgentAccessPolicy、SseChannel 词条；AgentAccessPolicy 词条的 _Avoid_ 注明 SandboxPolicy。
