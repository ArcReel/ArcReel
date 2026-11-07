# Stage reviewer contract

After every issue in a stage is integrated, review the entire stage diff, fix it, and deliver a **green HEAD** to the team-lead for stage PR creation.

Inputs: stage branch, stage worktree, Spec (Spec batches only), this stage's issues, batch handoff directory, and absolute stage handoff path.

1. Confirm the stage worktree, branch, and latest remote commit agree. Read the Spec if present, every stage issue's body and comments, and their handoffs. Always write Git commands as `git -C <absolute-stage-worktree-path>`.
2. Invoke `code-review` through the Skill tool with `origin/main` as the fixed point and the supplied Spec and stage issues as spec sources. Do not count requirements belonging to other stages or skipped requirements as missing. Refer Spec requirements with no issue coverage to the team-lead as Spec gaps.
3. Judge findings against the actual diff and acceptance boundaries. Before asserting a behavioral defect, reproduce it using actual production-caller usage. Fix valid findings and resolve temporary implementations whose issue handoffs specify stage integration as the removal condition. Consult the team-lead if the fix approaches a rewrite or requires the user under [escalation.md](../../../../docs/agents/escalation.md); otherwise decide yourself and leave a one-line reason in the handoff.
4. Run full gates for every domain touched by this stage diff with `uv run python scripts/gate.py --changed`. Implementers ran only relevant tests; this is the stage's first full gate. Push fixes as integration-fix commits with messages following [the `CONTRIBUTING.md` commit conventions](../../../../CONTRIBUTING.md). If the gate gets stuck (the same failure signature appears a second time), record a `fault` and report to the team-lead.
5. Once you have a **green HEAD** and clean worktree, perform the “Closing self-check” in [lessons.md](../../../../docs/agents/lessons.md), append an “Integration review” section under [handoff.md](handoff.md), report HEAD SHA and validation results to the team-lead, and stop.
