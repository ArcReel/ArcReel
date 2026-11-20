# Claude 记忆机制的官方材料

本次调研于 2026-09-03 为 [#2308](https://github.com/ArcReel/ArcReel/issues/2308) 汇总 Anthropic 一手材料，涵盖 Claude Code、Claude 平台与 Claude.ai。三个产品的行为各自成立。ArcReel 的术语与实现见[术语表](../../GLOSSARY.md)和 [ADR 0074](../adr/0074-agent-memory-two-level-storage.md)。SDK 在固定版本下的行为证据另见[实测记录](agent-auto-memory-under-sdk.md)。

| 问题 | 当时可确认的官方口径 |
| --- | --- |
| 指令、记忆与会话历史的边界 | Claude Code 将人工维护的 `CLAUDE.md` 与 Agent 保存的 auto memory 分离，会话历史则另行保留。Claude 平台的 memory tool 由客户端自行实现持久化，存储位置与授权责任均归属宿主应用。[Claude Code 记忆文档](https://code.claude.com/docs/en/memory) · [memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool) |
| 记什么 | Claude Code 公开材料列出用户、纠正、项目与参考资料四类，强调跨会话复用价值。已写在指令文件中、或可直接从项目与代码推导的信息应留在原处；一次性请求是否保留，取决于未来的相关性。 |
| 如何组织 | Claude Code 在启动时注入 `MEMORY.md` 索引的一部分，主题文件则按需读取。收录时的 200 行或 25 KB 限制属于特定版本观察，引用时需按 SDK 固定版本核实。[记忆文档](https://code.claude.com/docs/en/memory) · [变更记录](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md) |
| 自主写入与用户编辑 | Claude Code 以 Markdown 存储记忆，通过 `/memory` 提供管理入口；用户可自行编辑或删除，Agent 后续仍会持续更新。Claude.ai 于 2026-08-25 推出逐项管理记忆的功能，用户可在设置或对话中直接发起更正。[Claude Code 记忆文档](https://code.claude.com/docs/en/memory) · [Claude.ai 发布说明](https://claude.com/blog/claudes-memory-works-everywhere-and-you-decide-whats-in-it) |

## 采用材料时的限制

- 官方文档未公开主会话完整的 auto memory 系统提示词，也未承诺当用户编辑与自动写入冲突时以哪一方为准。
- memory tool、上下文编辑、上下文压缩与独立子会话各自使用不同接口。上下文即将被清除可作为提示模型提前记录关键信息的理由，但不能据此断言某款产品必定内置了这一触发机制。[上下文编辑](https://platform.claude.com/docs/en/build-with-claude/context-editing) · [上下文压缩](https://platform.claude.com/docs/en/build-with-claude/compaction)
- 本次调研未证明所有 Claude 产品都采用一致的记忆作用域与覆盖规则。早期的术语草案已由根术语表取代，原始引用摘录与产品对照见[调研快照](https://github.com/ArcReel/ArcReel/blob/fed952b5f25fb48a9b26e70a5878705c9ecbf988/docs/research/agent-memory-definition-and-practice.md)。
