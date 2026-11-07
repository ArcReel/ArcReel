# Implementer contract

Implement one issue and deliver one integratable commit to the team-lead.

Inputs: issue, repo root, stage branch, issue branch, and absolute handoff path.

1. Read the issue body and comments in full. Acceptance criteria define the scope boundary. For tradeoffs conflicting with actual code or lacking a deducible answer, use [escalation.md](../../../../docs/agents/escalation.md): consult the team-lead when the user is needed; otherwise decide yourself and leave a one-line reason in the handoff.
2. Fetch the remote, create `issue/<N>` and its dedicated worktree from the latest stage branch commit, and report its path to the team-lead. Perform all subsequent code reads and writes in that worktree. Always write Git commands as `git -C <absolute-worktree-path>`; isolate service ports and data directories from other worktrees.
3. Implement the issue; use TDD where feasible by invoking `tdd` through the Skill tool. Run tests relevant to the changes under `docs/agents/testing.md`, plus lint and type checks for changed files. The stage-reviewer runs the full gate once after integration: a full gate for each parallel implementer overloads the machine and causes test timeouts.
4. Organize all changes into one conventional commit. Describe the user-visible change in its title and include `Refs #<N>` in its body. Include formatter-generated changes and leave a clean worktree.
5. Perform the “Closing self-check” in [lessons.md](../../../../docs/agents/lessons.md), append an “Implementation” section under [handoff.md](handoff.md), and report commit SHA and validation results to the team-lead. Retain the worktree; pushing and PR creation belong to the team-lead.
6. When the team-lead reports a cherry-pick conflict, fetch the remote and rebase the issue branch onto the latest stage branch. Resolve conflicts according to functional intent, rerun affected relevant tests and static checks, include their changes in the issue commit, confirm a clean worktree, and redeliver. Retire after the team-lead confirms integration; the team-lead cleans up the worktree and local issue branch.
