---
name: video-workflow
description: Orchestrate an ArcReel video project when the user asks to create a video, start or resume a project, continue, take the next step, check progress, finish, or export. Use this for end-to-end workflow requests, not an isolated edit to one existing artifact.
---

# ArcReel video workflow

Use the connected ArcReel MCP server as the only source of project state. Every project-scoped tool call must name the project explicitly; never depend on local project files or the agent host's working directory.

## Route the plan

1. Resolve the project with ArcReel's project tools. If there is no project yet, collect the creation inputs and create one before continuing.
2. Call `get_workflow_plan` for that project. Include an episode only when the user selected one. Preserve any still-valid transient choices returned or requested by the plan.
3. Treat `workflow_plan.next_action` as authoritative. Execute only that action, using the available ArcReel tool whose live description covers it. Pass the project, `next_action.args`, target fields, and non-empty `requested_ids` as applicable; do not infer a different stage from filenames, prior messages, or artifact presence.
4. If the action requires a user choice or confirmation, explain its effect and wait for explicit consent. If it is `none`, show the blockers and stop changing the project. If it is `export`, report that the workflow is ready and hand off to the WebUI or embedded host because remote MCP does not compose or export the final video. If no available ArcReel tool can perform the action, report the missing capability instead of attempting local file access.
5. After the action completes, call `get_workflow_plan` again and route its new `next_action`. For `wait_for_task`, poll only by following the returned `poll_after_seconds`.

Read these references only when their topic applies:

- [Plan safety and confirmations](references/plan-safety.md) for blockers, transient choices, billable actions, and stale artifacts.
- [Content and generation modes](references/generation-modes.md) when interpreting mode-specific structures or references.
- [Generation results](references/generation-results.md) when selecting IDs or reporting batch outcomes, tasks, provider submission, and artifact status.
