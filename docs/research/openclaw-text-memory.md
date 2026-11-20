# OpenClaw 记忆机制取样

2026-09-03 查阅了 OpenClaw `2026.8.1` 的固定提交 [`d5912c2`](https://github.com/openclaw/openclaw/tree/d5912c2a0d14f26b745bd4aceb4431e532b6b2cd)，并将文档站中的记忆机制、工作区说明及 AGENTS.md 模板与仓库版本逐一核对。本次取样服务于 [ArcReel #2306](https://github.com/ArcReel/ArcReel/issues/2306) 的设计讨论。ArcReel 实际采用的分层结构与存储路径见 [ADR 0074](../adr/0074-agent-memory-two-level-storage.md) 及 [ADR 0088](../adr/0088-data-root-layered-projects-subdirectory.md)。

## 观察及其设计价值

| 机制 | 取样观察 | 对设计讨论的价值 |
| --- | --- | --- |
| 每日记录与长期精选 | 每日文件按写入日留存笔记；`MEMORY.md` 与 `USER.md` 则承载长期内容和用户画像。后台整理可从每日笔记提取长期条目 | 记录与筛选能分时处理。若另建后台整理，需要承担调度、并发与过期治理。[记忆架构][architecture] · [后台整理][dreaming] |
| 加载和按需检索 | 长期文件按不同预算注入，历史笔记可用 `memory_search` 定位、`memory_get` 读取。子会话的启动文件有独立范围 | 注入完整索引与按需读取正文可以组合。检索、预算和子会话范围均依赖实际运行时。[记忆][memory] · [工作区][workspace] |
| 压缩前写入 | 接近上下文压缩时可触发一次写入提醒。这个回合只追加到当日文件，长期文件只读 | 独立触发和专用写入规则可以收紧副作用。产品使用什么触发器，应按现有 SDK 能力及实际需求决定。[压缩][compaction] · [写入计划源码][flush] |
| 偏好更正与出处 | 用户行为指引有日期和生效状态，相互矛盾的指引不应同时生效。带风险的记忆保留适用条件、何时到期和出处，外部内容受来源约束 | 记忆应能解释一条偏好何时适用、由谁提供。新偏好替代旧偏好时，需要处理冲突。[用户画像][user-model] · [来源规则][provenance] |
| 用户编辑 | 工作区笔记是可编辑文件，整理流程用内容校验减少并发覆盖，也会淘汰过时内容 | 「用户可以编辑」和「用户内容不会被自动改写」是两项独立承诺。本次材料仅支持前者。[记忆架构][architecture] · [工作区模板][template] |

在本次取样中，OpenClaw 通过每日记录、后台整理与按需检索组成了多层工作流；Claude Code 则以启动时加载索引、按需读取主题文件为主。关于 Claude 的相关证据，见[官方记忆材料](agent-memory-definition-and-practice.md)与[SDK 固定版本实测](agent-auto-memory-under-sdk.md)。

## 未验证范围

未部署 OpenClaw，也未运行压缩、检索、并发写入或编辑界面测试。材料未能确认带时分后缀的笔记可以完全参加与日期文件相同的整理流程，也未能确认所有记忆均可通过界面编辑、一次删除或受同一加载预算限制。更细的源码检索记录见[原始调研快照](https://github.com/ArcReel/ArcReel/blob/fed952b5f25fb48a9b26e70a5878705c9ecbf988/docs/research/openclaw-text-memory.md)。

[architecture]: https://github.com/openclaw/openclaw/blob/d5912c2a0d14f26b745bd4aceb4431e532b6b2cd/docs/concepts/memory-architecture.md
[dreaming]: https://github.com/openclaw/openclaw/blob/d5912c2a0d14f26b745bd4aceb4431e532b6b2cd/docs/concepts/dreaming.md
[memory]: https://github.com/openclaw/openclaw/blob/d5912c2a0d14f26b745bd4aceb4431e532b6b2cd/docs/concepts/memory.md
[workspace]: https://github.com/openclaw/openclaw/blob/d5912c2a0d14f26b745bd4aceb4431e532b6b2cd/docs/concepts/agent-workspace.md
[compaction]: https://github.com/openclaw/openclaw/blob/d5912c2a0d14f26b745bd4aceb4431e532b6b2cd/docs/concepts/compaction.md
[flush]: https://github.com/openclaw/openclaw/blob/d5912c2a0d14f26b745bd4aceb4431e532b6b2cd/extensions/memory-core/src/flush-plan.ts
[user-model]: https://github.com/openclaw/openclaw/blob/d5912c2a0d14f26b745bd4aceb4431e532b6b2cd/docs/concepts/user-model.md
[provenance]: https://github.com/openclaw/openclaw/blob/d5912c2a0d14f26b745bd4aceb4431e532b6b2cd/docs/concepts/memory-provenance.md
[template]: https://github.com/openclaw/openclaw/blob/d5912c2a0d14f26b745bd4aceb4431e532b6b2cd/docs/reference/templates/AGENTS.md
