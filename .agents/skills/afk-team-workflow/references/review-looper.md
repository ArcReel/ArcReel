# Stage AI review loop contract

Advance one stage PR to a **green HEAD**, converged review, and merge readiness. Convergence follows the completion criteria in `pr-ai-review-loop`, not whether every bot uses approval wording.

Inputs: PR, stage branch, stage worktree, this stage's issues, batch handoff directory, absolute stage handoff path, and optional `rounds` budget in the form `evaluation-point/hard-stop`.

1. Confirm the stage worktree, branch, and latest remote commit agree. At initial startup this commit is the latest delivered **green HEAD**; at takeover it is the predecessor's hard-stop HEAD. Read every stage issue and its handoff, using existing stage-handoff sections as the already-decided starting point. Always write Git commands as `git -C <absolute-stage-worktree-path>`.
2. Mark the PR ready.
3. Invoke `pr-ai-review-loop` through the Skill tool, following its engineering judgment, coverage reconciliation, waiting, round, and final-check conventions. The delegator is the team-lead; use the supplied `rounds` budget, or the skill's default if omitted.
   - Run long tasks such as wait.sh and full gates as blocking foreground commands.
   - Push ordinary fixes as additional integration-fix commits with messages following [the `CONTRIBUTING.md` commit conventions](../../../../CONTRIBUTING.md). If the full gate gets stuck (the same failure signature appears a second time), record a `fault` and report to the team-lead.
   - Send evaluation reports, matters requiring the user under [escalation.md](../../../../docs/agents/escalation.md), and failure questions to the team-lead.
   - At the hard stop, complete the final necessary rereviews and final checks under that skill. If every new finding can be closed by a reply, reply and proceed to step 6. If code changes are needed, append a “Review loop” section under [handoff.md](handoff.md), perform the “Closing self-check” in [lessons.md](../../../../docs/agents/lessons.md), report the hard stop to the team-lead, and stop.
4. On a rebase instruction, rebase onto the latest `origin/main`, resolve conflicts, rerun the full gate, push with `--force-with-lease`, and record old and new HEADs under [handoff.md](handoff.md). Preserve one conventional commit and `Refs #<N>` per issue.
5. Classify reviewer findings outside batch scope according to that skill; for preexisting findings, reply with the boundary and record follow-up candidates. Consult the team-lead for findings requiring the user under escalation.md and acknowledge its decision upon receipt. The team-lead persists issue state under the main workflow's pause boundaries and blocks the current stage's merge until the user decides and corresponding recovery or rebuilding is complete.
6. After final checks pass, squash every non-issue commit in this stage into one integration-fix commit following the same conventions. Verify the tree is identical before and after squashing, then push with `--force-with-lease`. Once required checks pass for the new HEAD, perform the “Closing self-check” in lessons.md, append a “Review loop” section under [handoff.md](handoff.md), report the qualifying HEAD and round count, and stop.
