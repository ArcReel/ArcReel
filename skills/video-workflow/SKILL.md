---
name: video-workflow
description: 当用户要求创建视频、新建或继续项目、推进下一步、检查进度、完成或导出时，编排 ArcReel 视频项目。用于端到端工作流请求，不用于仅编辑某个现有产物。
---

# ArcReel 视频工作流

以已连接的 ArcReel MCP 服务作为项目状态的唯一来源。每次项目级工具调用都必须显式指定项目，不依赖本地项目文件或 Agent 宿主的工作目录。

## 按计划路由

1. 使用 ArcReel 项目工具确定目标项目。若尚无项目，先收集必要信息并创建项目。
2. 为项目调用 `get_workflow_plan`。用户已选定分集时，按 `episodes[]` 的播出顺序或标题定位，再取该集 `episode` 字段作为 `episode_id`。向用户称呼一集时使用播出位置与标题。重查计划时，带上仍然适用、已获用户确认的 `confirmed_request_durations`。
3. 若用户只要求查看进度或了解下一步，仅汇报计划中的内容现状（`status.content`）、当前问题和 `next_action`，不修改项目。
4. 用户要求推进时，依据 `workflow_plan.next_action` 路由并执行该动作；`next_alternatives` 非空表示分岔，列出并列选项由用户选定。按需向对应的 ArcReel 工具传入 project、`next_action.args`、target 字段和非空 `requested_ids`。
5. 若用户点名执行某项操作，先查看 `status.operations`：为 `admitted` 时直接执行；为 `refused` 时向用户转述 `reason` 及补齐方法；为 `not_applicable` 时说明该操作不适用。对未列出的操作，按对应工具入口的准入结果处理；入口返回 `operation_not_admitted` 时读取 `params.reason`。
6. 当动作需要用户选择或确认时，先说明影响并等待明确同意。`blockers` 非空时报告原因并暂停制作；涉及数据升级时，按计划给出的恢复动作处理。收到 `create_edit_timeline` 时，调用 `create_timeline`，其中 `episode` 取 `next_action.args.episode_id`，`from` 传入 `script`。收到 `none` 且计划中 `edit` 步骤为 `completed` 时，说明整个工作流已完成；其余无阻断的 `none` 情况，向用户报告 `status.issues`。
7. 生成动作返回 `generation_batch` 时，保留 `batch_id`，并按每次返回的 `poll_after_seconds` 调用 `get_generation_batch`，直到结果为 `done: true`。之后再调用 `get_workflow_plan`，按新的 `next_action` 路由。计划因已有任务返回 `wait_for_task` 时，等待其 `poll_after_seconds` 后再次调用 `get_workflow_plan`，最多执行 `max_poll_attempts` 次；达到上限后相同任务仍在运行，则报告其 `task_ids` 并停止。其他动作完成后直接刷新计划。

仅在主题适用时读取对应参考：

- 遇到阻断、临时选择、计费动作或过期产物时，读取[计划安全与确认](references/plan-safety.md)。
- 解释不同模式的结构或引用时，读取[内容与生成模式](references/generation-modes.md)。
- 选择 ID，或报告批次结果、任务、供应商提交和产物状态时，读取[生成结果](references/generation-results.md)。
