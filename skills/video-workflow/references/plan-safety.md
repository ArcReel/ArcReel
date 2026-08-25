# Plan safety and confirmations

`get_workflow_plan` owns step order, applicability, blockers, targets, and the single next action. Do not reconstruct a workflow from files, artifact buckets, or conversation history. An empty asset category can be a valid completed result.

Values such as narration delivery and confirmed request durations belong to one request rather than persistent project settings. Carry them into later plan calls only while the user's choice still applies. Never invent a value merely to satisfy a required argument.

Stop all mutations when the plan reports blockers or `next_action.type` is `none`. Use the structured problem code, action, path, and details; do not parse prose to choose a recovery. Formal project data must be changed through ArcReel's transactional tools. If the available remote tools cannot repair it, explain the limit instead of editing storage directly.

Get explicit consent before destructive changes or any action that the plan or tool identifies as billable. Video batch admission is all-or-nothing: when admission is blocked or needs confirmation, enqueue nothing, explain each affected unit, resolve the reported action, and request a fresh plan.

Stale artifacts remain usable and do not authorize regeneration. Regenerate current or stale work only when the user explicitly selects it. Never delete or overwrite paid history automatically.
