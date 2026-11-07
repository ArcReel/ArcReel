# Fixer contract

Fix failures reported by qa-verifier for a stage and deliver a new **green HEAD** to the team-lead.

Inputs: stage branch, stage worktree, absolute path to QA `results.md`, this stage's issues, batch handoff directory, and absolute path to the stage handoff.

1. Confirm the stage worktree, branch, and latest remote commit agree. Read failures and their evidence, corresponding issues, and handoffs. Always write Git commands as `git -C <absolute-stage-worktree-path>`.
2. Reproduce each failure using its reproduction steps before fixing its root cause. Where feasible, invoke `tdd` through the Skill tool and lock it down with a test that fails before the fix. If reproduction establishes an acceptance misreading rather than a defect, do not change code; document the evidence in your report. Consult the team-lead when the fix requires the user under [escalation.md](../../../../docs/agents/escalation.md).
3. Run relevant tests under `docs/agents/testing.md`, then the full gate with `uv run python scripts/gate.py --changed`. Push fixes as integration-fix commits with messages following [the `CONTRIBUTING.md` commit conventions](../../../../CONTRIBUTING.md). If the gate gets stuck (the same failure signature appears a second time), record a `fault` and report to the team-lead.
4. Once you have a **green HEAD** and clean worktree, perform the “Closing self-check” in [lessons.md](../../../../docs/agents/lessons.md). Append a “QA fixes” section under [handoff.md](handoff.md), report the HEAD SHA and disposition of every failure to the team-lead, then stop.
