---
status: accepted
---

# Agent 工具集单源声明，宿主差异只允许两轴

Agent 工具集的每个工具只有一份宿主无关声明：名字、中文完整描述（参数说明写在请求模型字段上）、由 pydantic 请求模型派生的 schema、迁移阻断策略（必填，不设默认）、domain key 与 handler。ArcReel Agent 与外部 Agent 两个宿主各持一个薄 adapter，只把这份声明投影成各自的注册形式与结果信封。两宿主之间允许的差异只有两轴：项目如何确定（仅对作用于某个既有项目的工具：ArcReel Agent 由会话决定、schema 不暴露 `project`；外部 Agent 每次显式指定。`list_projects`、`create_project` 没有目标项目，两宿主都不带 `project`）与长任务是否等待结果（ArcReel Agent 等到终态；外部 Agent 立即拿到批次句柄，描述里的轮询说明由「长任务」标记派生）。其余一律同源：adapter 不改写 problem code、不另起 domain key、不增删结果字段——同一个 `ToolOutcome` 在两宿主编码出相同的 JSON，ArcReel Agent 拿到的是摘要文本加上这份 JSON，而不是只给摘要。长任务因第二轴而例外：两宿主拿到的是不同阶段的 `ToolOutcome`（ArcReel Agent 是终态生成结果，外部 Agent 首次是批次句柄），一致性落在外部 Agent 轮询到终态时的批次查询结果上，其中附带的生成结果与 ArcReel Agent 拿到的同形。

曾经两个宿主各写一份声明，漂移在一个月内就出现了：错误码被改写成 `internal_error`、同一工具两个 domain key、迁移拒绝三种形状、四个入口漏声明迁移阻断，而领域测试挂在一个会被 SDK 丢弃字段的信封上。

## Considered Options

- **按宿主取长补短**（外部 Agent 用英文短描述省 token、ArcReel Agent 只给摘要省上下文）：否决。每一处「宿主专属」都会变成下一次漂移的入口，而两宿主服务的是同一类创作 Agent，契约没有理由不同。
- **保留手写的 `MIGRATION_BLOCKED_TOOL_IDS` 集合，让外部 Agent 的 adapter 也读它**：否决。集合对新工具没有强制力；把阻断策略做成声明的必填字段，才是 ADR 0062「阻断以入口声明为准」的落实方式。

## Consequences

- 领域行为在 handler（`ToolOutcome`）层测一次；adapter 不承载领域断言，只有一个按声明表驱动的双宿主一致性测试与少量信封测试。
- `ARCREEL_MCP_TOOL_IDS` 与迁移阻断集合都由声明派生，名字保留给既有消费方（profile lint、前端工具显示名 i18n 测试）。
