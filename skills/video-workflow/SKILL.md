---
name: video-workflow
description: 当用户要求创建视频、新建或继续项目、推进下一步、检查进度、完成或导出时，编排 ArcReel 视频项目。用于端到端工作流请求，不用于仅编辑某个现有产物。
---

# ArcReel 视频工作流

以已连接的 ArcReel MCP 服务作为项目状态的唯一来源。每次项目级工具调用都必须显式指定项目，不依赖本地项目文件或 Agent 宿主的工作目录。

## 按计划路由

1. 使用 ArcReel 项目工具确定项目。尚无项目时，先收集创建所需信息并创建项目。
2. 为该项目调用 `get_workflow_plan`。仅在用户已选定分集时传入 episode。保留计划返回或要求的、仍然有效的临时选择。
3. 以 `workflow_plan.next_action` 为权威。只执行该动作，并使用实时描述覆盖该动作的 ArcReel 工具；按需传入 project、`next_action.args`、target 字段和非空 `requested_ids`。不得根据文件名、历史消息或产物是否存在推断其他阶段。
4. 动作需要用户选择或确认时，说明影响并等待明确同意。`next_action.type` 为 `none` 时展示阻断原因并停止修改项目；为 `export` 时说明工作流已就绪，并移交 WebUI 或内嵌宿主，因为远程 MCP 不负责合成或导出成片。没有可执行该动作的 ArcReel 工具时，报告能力缺口，不尝试读取宿主本地文件。
5. 生成动作返回 `generation_batch` 时，保留 `batch_id`，并按每次返回的 `poll_after_seconds` 调用 `get_generation_batch`，直到结果为 `done: true`。之后再调用 `get_workflow_plan`，按新的 `next_action` 路由。计划因已有任务返回 `wait_for_task` 时，等待其 `poll_after_seconds` 后再次调用 `get_workflow_plan`，最多执行 `max_poll_attempts` 次；达到上限后相同任务仍在运行，则报告其 `task_ids` 并停止。其他动作完成后直接刷新计划。

仅在主题适用时读取对应参考：

- 遇到阻断、临时选择、计费动作或过期产物时，读取[计划安全与确认](references/plan-safety.md)。
- 解释不同模式的结构或引用时，读取[内容与生成模式](references/generation-modes.md)。
- 选择 ID，或报告批次结果、任务、供应商提交和产物状态时，读取[生成结果](references/generation-results.md)。
