# Take over an unfinished batch

Read this page only when a new team-lead must take over from durable facts, or the user explicitly requests recovery / reconciliation. Continuations in the same session use the current runtime context directly.

1. Read `.afk/<batch-id>.jsonl` to recover scope and query remote issues, stage PRs, and branches. Issue labels are the remote truth for pause status; the ledger only supplements reasons.
2. Stop old agents and clean up this batch's stale local worktrees / branches and unintegrated handoff files.
3. Resume from remote truth: advance to the next stage after merged stages; rebuild a stage worktree from an open PR's remote HEAD and resume its review loop; otherwise derive completed issues from `Refs #<N>` commits on the remote stage branch. Treat all changes absent from the remote as unfinished.
4. Merge authorization and cost budgets in the predecessor's transcript cannot be inherited. The new team-lead must request merge authorization again for unfinished stages before the first merge, and request a new budget before deployment smoke testing.

If the user chooses to restart, close in-flight stage PRs, stop this batch's agents, clean up its workspace / worktrees / branches, append `closed`, then plan a new batch from section 1 of SKILL.md.
