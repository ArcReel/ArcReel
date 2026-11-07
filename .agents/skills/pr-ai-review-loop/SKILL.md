---
name: pr-ai-review-loop
description: Converge PR AI review. Use when a user or team-lead requests starting or continuing a review-and-fix loop; not for producing feedback as a GitHub reviewer or handling only one comment.
---

# Converge PR AI review

Aim for mergeable code quality. Completion means every finding has a disposition, not that every bot has stopped suggesting changes.

The delegator is the user or team-lead who starts the loop. Before entering, confirm a non-draft PR exists, the checkout matches its latest HEAD, and you understand its acceptance boundaries. PR creation, marking ready, and merging belong to the delegator or higher-level workflow.

## Engineering judgment

Findings are leads to verify: a finding is valid only when you can reconstruct its trigger path and incorrect result from code and contracts. Close unconfirmed findings as false positives or YAGNI. You also decide whether a finding belongs to this PR and how to fix it. AI reviewers suggest changes for any imaginable path; every accepted suggestion creates more code to review next round. Judgment produces a disposition; modification is only one kind.

**Classify.** Read the whole batch and classify every finding before changing code. Fix only **defects** belonging to this PR; close **YAGNI**, **nitpicks**, **preexisting issues**, and **false positives** with replies. Route preexisting issues under [escalation.md](../../../docs/agents/escalation.md). Defects must be routinely reachable; see “Defend only against routinely reachable scenarios” in [general.md](../../../docs/standards/general.md). Severity labels are reviewer estimates; classification is your conclusion.

**Evidence.** Assess actual consequences from project contracts, real call paths, and trust boundaries. A trigger path is only the starting point; explain why current behavior is wrong. Prefer a test that fails before and passes after the fix for executable behavioral defects. If stable reproduction is difficult, support the finding with an explicit code causal chain or interface contract. Prioritize verifying security and data-integrity risks, but judge fixes independently; use escalation.md to decide whether remaining tradeoffs need the delegator's decision.

**Root cause.** Fix the root cause rather than symptoms. Reviewer locations and patches usually stop at symptoms; make changes in the layer responsible for the guarantee, using existing invariants, abstractions, and error handling. Adding defenses at the symptom is a sign of patchwork. Judge the smallest fix by final design clarity rather than line count. After verification, read the entire PR diff to confirm the rounds' changes still form a coherent implementation.

**Convergence.** Subsequent reviews focus on accepted fixes and regressions they introduce; still classify new findings first. Reopen decided topics only when new evidence appears or relevant premises change. If multiple rounds add patches around the same place, return to the root cause and reorganize the implementation instead of accumulating local defenses.

## Advance the loop

On first entry, read [reviewers.md](references/reviewers.md) and follow each reviewer's protocol to confirm coverage, read findings, and request necessary rereviews. Run the commands below from this skill's directory; `<repo-root>` always points to the target PR checkout. Script header comments define arguments and fields.

```bash
bash scripts/poll.sh --repo-root <repo-root> <PR_NUMBER>
bash scripts/query.sh --repo-root <repo-root> <PR_NUMBER> details <id>...
```

Use the index for navigation and read bodies as needed. `no_change` only means the index has not changed; after context loss, recover with `query.sh ... index`. New findings carry `triage_hint`, prompting classification again.

Combine all reviewers' findings for the round, CI failures, and new security alerts. Classify the complete batch, then fix, run relevant tests and gates, and push together. If CI's root cause is already fixed on main, synchronize main and reverify. Record every disposition under “Write back dispositions” in reviewers.md; findings with one root cause may reference the same disposition.

Count each disposed batch as one round, including batches closed entirely by replies without a push:

```bash
bash scripts/round.sh --repo-root <repo-root> <PR_NUMBER> mark --implemented <n> --pushback <n> --note "root cause and disposition"
```

`--pushback` counts findings closed with replies. CI fixes, rebases, and trigger commands alone do not count as rounds. Continue polling status; run `bash scripts/wait.sh --repo-root <repo-root> <PR_NUMBER>` in the foreground only when review or check results are missing and no actionable work remains. Handle timeouts, quotas, permissions, or bot failures under [faults.md](references/faults.md).

## Completion criteria

Before declaring convergence, confirm the current HEAD meets all of these:

- **Valid coverage**: every participating reviewer's review is complete and covers current changes; see “Identity and triggering” in reviewers.md for permitted reuse. Report fault-disabled reviewers separately; do not count them as passing.
- **Findings disposed**: every substantive finding in this loop, including historical inline comments, review bodies, and summaries, is classified. Defects are fixed; others have recorded replies. No important risk remains undecided. Recording the disposition suffices; do not wait for bot agreement or retraction.
- **Checks pass**: relevant tests, gates, and required checks for the current HEAD pass, and CodeQL analysis and new security alerts satisfy “CodeQL security exit threshold” in reviewers.md. Empty `checks_failing` does not mean every check is complete.

Poll again for final checks. Query `query.sh ... unacked <bot[bot]>` once each for CodeRabbit and Codex, using `history` / `details` to read any bodies not yet checked. Judge whether old findings were missed by actual fixes and PR replies, not bot acknowledgment counts. If HEAD changes during final checks, reconcile again against the new HEAD.

## Budget and handoff

The default evaluation point is **3** and hard stop **6**; the delegator may override them. Recover the ledger with `round.sh ... show`; takeovers retain the cumulative round count. At the evaluation point, briefly report whether progress is still removing real defects or mainly cycling and expanding scope. Keep classification standards consistent across rounds, without loosening or tightening them near the budget.

At the hard stop, wait only for the last batch's necessary rereviews and perform final checks. Exit if completion criteria are met; otherwise stop and hand off unresolved matters. Exhausting the budget does not mean quality passes.

Refer matters requiring the user under escalation.md to the delegator promptly. For reviewer conflicts and other disagreements, verify evidence first, then judge yourself. On exit, report final HEAD, round count, verification results, key fixes, reasons for leaving findings unchanged, and unresolved matters. For leads beyond existing records or scope, attach evidence and distinguish facts from guesses: hand them to a team-lead delegator as follow-ups; otherwise route them yourself under “Handling matters that do not need the user” in escalation.md. Do not repeat leads already covered by existing records. Before exiting, perform the “Closing self-check” in [lessons.md](../../../docs/agents/lessons.md) and post candidates as `lesson-candidate` comments on this PR.
