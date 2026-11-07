# Cross-harness delegation with Herdr

First invoke `herdr` through the Skill tool. Use native harness team capabilities for same-harness teammates and Herdr for cross-harness teammates.

## Workspace

1. Create a workspace labeled `afk:<batch-id>` for the batch and retain the workspace, tab, and pane IDs from the JSON response. Keep the root pane open as an anchor; otherwise closing the final tab reclaims the workspace. Place all Herdr teammates for this batch in this workspace, creating tabs per issue / stage as needed.
2. Set pane cwd to the actual worktree. Native agent args retain the harness's permission / sandbox settings; additional writable directories include `<repo-root>/.afk/<batch-id>/`. For codex teammates using linked worktrees, also include the main repository's `<repo-root>/.git`.
3. Inject only `batch-id`, the current agent name / pane ID, and the team-lead pane ID in the startup prompt. Do not inject addresses of other sessions or teammates.
4. Deliver the startup prompt with `agent prompt --wait --until working --until blocked`; matching either state counts as delivery. Without `--until`, it waits until the teammate finishes the turn.
5. Newly started agents occasionally swallow the first prompt. On `agent_prompt_stalled`, send Enter first: the prompt may be sitting in the input field while the long paste collapses to `[Pasted text #N +M lines]`, invisible in pane text. Resend only if it remains idle after Enter.

The workspace is a topology and lifecycle boundary, not a messaging permission boundary. Close only the workspace created for this batch during cleanup.

## Return notifications

Teammates send messages only to the team-lead pane ID injected into their prompt. Include `batch-id`, issue / stage, your agent name / pane ID, the `handoff` or `request` type, and a one-sentence summary.

- `handoff`: write durable deliverables such as a commit or handoff first, then notify with the path or SHA.
- `request`: use for matters and failure decisions that the role contract requires referring to the team-lead.

Messages only wake the recipient and locate context; keep full context in issues, commits, and handoffs. Do not put Herdr topology IDs in the ledger.

The team-lead treats handoff notifications as completion signals and request notifications as wake-ups requiring a decision. Reviewers and loopers periodically become idle while waiting for gates or CI. When supervising teammates, react only to blocked, disappeared, or prolonged-idle states.
