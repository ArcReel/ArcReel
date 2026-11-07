---
name: video-workflow
description: Orchestrate ArcReel projects when users request video creation, new/continued projects, next steps, progress, completion, or export. For end-to-end workflows, not merely editing an existing artifact.
---

# ArcReel video workflow

Use the connected ArcReel MCP service as the sole project-state authority. Every project-level tool call must explicitly specify the project, independent of local files or host cwd.

## Route by plan

1. Identify the project through ArcReel tools. If none exists, collect required creation information and create it.
2. Call `get_workflow_plan` for that project. Pass `episode_id` only for a user-selected episode: `episodes[]` order in project details is playback order; locate by ordinal/title, then pass its `episode` internal ID, not ordinal. Discuss episodes by playback position/title. Retain still-valid temporary choices returned or required by the plan.
3. For progress/next-step questions only, report current content (`status.content`), issues, and `next_action`, without project changes.
4. For requests to proceed, route and execute `workflow_plan.next_action`; nonempty `next_alternatives` indicates a branch, so list parallel options for user selection. Pass project, `next_action.args`, target fields, and nonempty `requested_ids` as needed to ArcReel tools.
5. For a named operation, inspect its admission in `status.operations`: execute `admitted` without waiting for it to become next; relay `refused` `reason` and repair guidance. Tool entries use the same decision, returning `operation_not_admitted` with `params.reason` matching the plan. `operations` describes only listed operations; generation entries retain their own admission (ADR 0073), whose refusal reasons must be relayed.
6. Explain impacts and wait for explicit agreement when choices/confirmation are required. Show nonempty `blockers` and stop modifications. For `next_action.type` `create_edit_timeline`, call `create_timeline` (`episode` from `next_action.args.episode_id`, `from` as `script`). `none` with `edit` `completed` in plan `steps` means the workflow is finished; other unblocked `none` states report `status.issues`.
7. When generation returns `generation_batch`, retain `batch_id` and poll `get_generation_batch` at each returned `poll_after_seconds` until `done: true`. Then refresh `get_workflow_plan` and route the new `next_action`. For `wait_for_task`, wait `poll_after_seconds` and requery `get_workflow_plan` at most `max_poll_attempts` times; if the same task persists, report `task_ids` and stop. Refresh immediately after other actions complete.

Read references only when applicable:

- Blockers, temporary choices, paid actions, or stale artifacts: [plan safety and confirmation](references/plan-safety.md).
- Explaining mode structures/references: [content and generation modes](references/generation-modes.md).
- Selecting IDs or reporting batches, tasks, provider submission, and artifacts: [generation results](references/generation-results.md).
