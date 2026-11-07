---
name: afk-team-workflow
description: Assemble a team to run all sub-issues of a Spec, or a batch of standalone issues, unattended until all are merged or explicitly paused.
disable-model-invocation: true
---

# AFK team workflow

You are the team-lead: advance a batch unattended until everything is merged and accepted, or explicitly paused. You own planning, scheduling, integration, and decisions; delegate all code and acceptance work to teammates. Once started, keep running until a terminal batch state. Resolve scheduling questions yourself; use [escalation.md](../../../docs/agents/escalation.md) to decide when to involve the user.

## 1. Plan the batch

The two batch types differ only in the table below; the remaining workflow is identical:

| | Spec batch | Standalone batch |
|---|---|---|
| Scope | `batch-poll.sh --spec <N>` | `--issues <N,...>` when the user provides numbers; `--ready` when asked to handle non-Spec `ready-for-agent` issues |
| Stage division | The fewest independently reviewable and mergeable stages; keep small batches in one stage | Issues in each stage must share one scoped conventional PR title; minimize stages subject to dependency order |
| Acceptance source | The Spec and this stage's issues | This stage's issues |
| PR references | `Closes #<N>`, plus `Refs #<Spec>` without automatically closing the Spec | `Closes #<N>` |
| Cleanup issue | Attach to the parent Spec under [issue-tracker conventions](../../../docs/agents/issue-tracker.md) | Create independently |
| Closing report | Post to the Spec issue | Post as a comment on the last stage PR |
| Ledger scope | `--scope-spec <N>` | `--scope-issues <N,...>` |

1. Run `scripts/batch-poll.sh --repo-root <repo-root>` with the scope arguments above. Then read every issue body and its comments in full to establish actual acceptance boundaries, the canonical dependency graph, triage, and claim status. `--ready` only filters mechanically; exclude research, evaluation, and other issues that do not produce code yourself.
2. Establish the frontier: only `OPEN` issues may enter stages and PR `Closes` lists. Among them, issues labeled `ready-for-agent`, unclaimed by others, and with completed blockers enter the frontier. Decide semantically when no triage label exists; keep `ready-for-human` issues and their blocked downstream issues outside the frontier.
3. Compare this batch's scope with scopes of non-`closed` ledgers in `.afk/` and parallel batches reported by the user. If scopes match, pause and let the user choose takeover or restart; read [recovery.md](references/recovery.md) before either. Include partial overlaps in the display in step 5.
4. Divide the dependency graph into delivery stages using the table. Before starting each role, choose model and effort under [model-routing](../model-routing/SKILL.md), recording the Category or route and a one-sentence reason. Use the Review route for stage-reviewer and `deep-low` for qa-verifier; both must use clean contexts uninvolved in any implementation in this stage.
5. Show the user stages, dependencies, model reasons, skipped items, and downstream impact; for `--ready` batches, also show selected and excluded lists for confirmation or removal. Request two authorizations at the same time: rebase merging every stage PR, and a budget for actual generation during deployment smoke tests.
6. Create a thin ledger with `scripts/ledger.sh --new`, recording scope, planning decisions, and authorization; it generates and prints this batch's `batch-id`. Record only facts that cannot be reconstructed from Git / GitHub.

## 2. Execute the task graph

Form the team and delegate using [spawn-prompts.md](references/spawn-prompts.md). If `HERDR_ENV=1`, first read [herdr-teammate.md](references/herdr-teammate.md); otherwise use the current harness's native team capabilities.

Execute stages strictly in sequence:

1. Create `afk/<batch-id>/stage-<K>` and its dedicated worktree from the latest `origin/main`, then push the stage branch.
2. Claim and delegate frontier issues whose dependencies are satisfied and whose change surfaces can safely run concurrently. Give each issue a separate worktree; the implementer delivers one issue commit under [implementer.md](references/implementer.md).
3. Serially cherry-pick delivered issue commits in the stage worktree and push. On conflict, abort; the original implementer resolves against the latest stage branch, verifies, and redelivers. An issue is complete and can unlock a new frontier only after its commit with `Refs #<N>` appears on the remote stage branch. Verify this first, then clean up the issue worktree and local branch with a separate command.
4. Before the final stage, aggregate follow-ups from all batch handoffs. For verified real defects within batch scope that do not require the user under escalation.md, create cleanup issues, attach them according to the table, and use the same relay; route the rest under escalation.md. For new findings produced by the cleanup round, open issues for real defects under “Real defects outside scope” in escalation.md regardless of batch scope, and route other findings under that same section. Then finish: do not start another cleanup round or involve the user.
5. After all issues are integrated, the stage-reviewer reviews the complete stage diff under [stage-reviewer.md](references/stage-reviewer.md) and delivers a **green HEAD**.
6. Have qa-verifier accept the green HEAD in full mode under [qa-verifier.md](references/qa-verifier.md). If anything fails, give all failures from this round to one new fixer under [fixer.md](references/fixer.md), then have qa-verifier recheck only those items. Repeat until none fail or remaining failures are **stuck** (the same failure signature appears a second time). Route stuck items by nature:
   - **Regression** (behavior that existed before the batch is broken): pause this issue and its downstream issues under section 3.
   - **New acceptance unmet** (existing behavior remains intact): leave the issue's commit in the stage, remove it from the PR's `Closes` list, comment a QA evidence summary on the issue, and keep it open and `ready-for-agent`.
7. Create a draft PR, using `Closes #<N>` as shown in the table for stage issues not left open. Post QA conclusions as one textual PR comment. Post lesson candidates from this stage's handoffs as `lesson-candidate` comments in the format in [lessons.md](../../../docs/agents/lessons.md). Put independent decisions from handoffs and their one-line reasons in the PR body. Start review-looper to converge AI review and commit history under [review-looper.md](references/review-looper.md).
8. After review-looper reports a qualifying HEAD, if commits since QA changed files other than tests and documentation, have qa-verifier use recheck mode for items affected by that commit range. On failure, fix under step 6; after the fixer pushes, reenter the review loop with the new HEAD.
9. Confirm the qualifying HEAD equals the current `headRefOid` and `mergeable=MERGEABLE`, then rebase merge using that `headRefOid` as expected-head; on mismatch, reenter review. Start the next stage from the latest `origin/main`.

## 3. Pause boundaries

If implementation, review, or acceptance reveals a matter requiring the user under escalation.md, or a Spec requirement with no issue coverage (Spec gap), pause the affected matter and its downstream issues. Ask the user in the escalation.md format while continuing the remaining frontier:

1. **quiesce first**: stop affected agents and discard unintegrated handoffs. If stage-reviewer, qa-verifier, fixer, or review-looper is running, stop it first and verify the worktree, branch, remote HEAD, and handoff.
2. Remove `ready-for-agent` and add `ready-for-human` on existing issues, record the reason, and remove the paused scope from the current stage.
3. If the user chooses to continue: restore labels on existing issues; for Spec gaps, first create and attach a sub-issue, then reschedule.
4. If the user chooses to keep the pause: rebuild the stage excluding that issue and its downstream issues only if related commits have entered the stage branch. Update any existing PR's `Closes` list. After rebuilding, rerun integration review, acceptance, and the review loop.

Handle recoverable runtime failures, repeated reviewer noise, and decisions not requiring the user yourself and record them. Pause unrecoverable failures blocking **green HEAD** as above. After review-looper reports a hard stop, decide unresolved matters and hand off to a new looper: provide extended `rounds`, and include rejected findings in the delegation for it to reply to.

## 4. Close out

1. **Deployment smoke test**: after all stages merge, target the batch's last merged commit that triggered a `docker-edge` build. Have a new qa-verifier use smoke mode on the deployed instance to verify the batch's main paths across stages and each stage's “pending deployment verification” items, capped by the authorized budget. For smoke failures, open issues under “Real defects outside scope” in escalation.md with evidence summaries; do not start a fix round. If no merge in this batch triggered an image build, skip smoke testing and mark pending deployment items unverifiable.
2. **Closing report**: post one message at the location in the table, organized by merged stage, listing PRs, QA and smoke conclusion counts, issues left open, and paused or skipped items. Follow with a human-action list: every item in each `results.md` whose current conclusion remains unverifiable, with its reason (smoke conclusions govern pending deployment items), and matters needing user decisions, combined in the “How to ask” format in escalation.md. Omit the human-action list if both are empty.
3. Remove assignees from claimed issues, clean up this batch's agents, worktrees, local branches, and Herdr workspace, then append the `closed` ledger line.

In the closing response, provide `batch-id`, the closing report link, and ledger, handoff, and evidence directory paths. Preserve these batch records. Append later user decisions on these matters as `decision` entries in this batch's ledger.
