---
name: lessons-sweep
description: Periodically inspect merged changes, update the lessons library, promote or retire standards by threshold, and produce an automatically merged sweep PR.
disable-model-invocation: true
---

# Lessons sweep

You are the sole writer of `docs/LESSONS.md`: verify and count lesson candidates since the last sweep, promote them by threshold, remove expired entries and obsolete rules, and produce a sweep PR. Read [lessons.md](../../../docs/agents/lessons.md) in full before starting for recording and promotion rules; this document contains only the procedure. Use [escalation.md](../../../docs/agents/escalation.md) to decide whether to involve the user or open issues for out-of-scope defects. Sweeps normally run unattended: open `needs-triage` issues for matters requiring the user and continue the rest.

## 1. Preflight

1. Fetch `origin`. List all open PRs with `gh pr list --state open --limit 1000 --json number,headRefName` (the default returns only 30) and find PRs whose head branch starts with `lessons-sweep/`.
2. If one exists, take it over and handle only that PR during this sweep. Leave the new window for the next sweep so windows remain contiguous and only one lessons-library change exists at a time. Explicitly pass its PR number to every `gh pr` command on the takeover path, and work in its head branch's worktree. Read blocking comments first, then inspect status with `gh pr view <PR-number> --json isDraft,autoMergeRequest,mergeStateStatus,statusCheckRollup`:
   - Auto-merge enabled, no conflicts, and no failed required checks: confirm and finish.
   - Same blocker as the previous takeover: if its `needs-triage` issue remains open, add a comment and finish; otherwise create one. If the issue is closed, apply its decision and proceed to the next item.
   - Otherwise: rebase onto the latest `origin/main`, run the gate from section 6 item 2, push with `--force-with-lease`, and update the PR body. If the PR is a draft, make it ready with `gh pr ready`, then execute section 6 item 4.
3. Read the `<!-- reviewed-through: <SHA> <date> -->` marker at the top of `docs/LESSONS.md`. The window is `<SHA>..origin/main`. Record the current `origin/main` SHA as the window endpoint, and create a `lessons-sweep/<YYYY-MM-DD>` branch and worktree from that endpoint. Perform subsequent changes there.

## 2. Collect candidates

Inspect every merged PR in the window. Ordinary PRs use squash merge and AFK stage PRs use rebase merge, so resolve PRs from commits: for every commit in `git log <SHA>..<window-endpoint> --first-parent`, call `gh api repos/{owner}/{repo}/commits/<sha>/pulls`, then deduplicate the PR list. Candidate comments are in PR issue comments; review feedback is in reviews and review comments. For squash-merged PRs, fix commits exist only in that PR's commits list.

- PR comments whose first line is `<!-- lesson-candidate -->`.
- Review feedback that was accepted and fixed, and fix commits within PRs.
- Revert commits and `fix`-type commits on main in the window.
- Failed CI runs on main in the window.

The last three categories have no ready-made candidate text. For those meeting the “Closing self-check” criteria in lessons.md, write candidates using the entry format.

## 3. Verify and count

Verify every candidate: check symptoms, root cause, and why safeguards missed it against code, diffs, and CI logs. Treat conclusions in comments only as leads. Discard invalid candidates and those failing the “Closing self-check” criteria in lessons.md.

Compare valid candidates with existing entries by “Pattern”: append an occurrence for the same kind, otherwise create an entry and group it according to “Grouping” in lessons.md. Use the counting unit specified there.

## 4. Clean up and retire

1. Remove entries using the expiration rules under “Counting and promotion” in lessons.md.
2. Using the statistical window under “Retiring standards” in lessons.md, count review-comment references to each rule heading in `docs/standards/`.
3. Follow “Retiring standards” for rules now enforced by checks, made impossible to violate by architecture, and never cited. List retained zero-reference rules in the PR body so future sweeps can identify the starting point for reconsideration.

## 5. Promote

For each entry reaching the promotion threshold, decide its destination under “Counting and promotion” in lessons.md. For entries with issue numbers: skip open issues; for closed issues, follow their decision, implement the destination if accepted, or remove the entry if rejected. Choose execution by change size:

- **Textual rule**: add it to the relevant `docs/standards/` file using the neighboring style; move the entry to “Promoted”.
- **Small, self-contained check** (an item in `scripts/audit_conventions.py`, ESLint `no-restricted-syntax`, or a test): implement in this PR, verify it passes on current code and fails on a counterexample to the lesson; move the entry to “Promoted”.
- **Other destinations** (skills, navigation pointers, architecture documentation, or checks requiring broad changes): open a `ready-for-agent` issue using the three steps under “Real defects outside scope” in escalation.md. Mark the entry “Promoted, see #<issue>” and move it to “Promoted”.
- **Meets escalation.md criteria for involving the user**: check for duplicates first, then open a `needs-triage` issue with options and a recommendation; leave the entry in its original group and note the issue number.

## 6. Produce the PR

If sections 3–5 produce no changes, advance only the reviewed-through marker in the PR so the next sweep need not rescan the same window. Execute in order:

1. Update the reviewed-through marker to the window endpoint SHA and date, and commit all changes from this sweep.
2. Run `uv run python scripts/gate.py --changed` until it passes.
3. Create a PR titled `chore(lessons): <YYYY-MM-DD> lessons sweep`. In its body list new lessons, count changes, removed lessons, promotions and destinations, retired rules, decisions made independently by the agent (each with a one-line reason), and the number of issues opened by agents in the window (count `ready-for-agent` and `needs-triage` separately).
4. If the PR only advances the marker, enable auto-merge directly with `gh pr merge --auto --squash`. Otherwise invoke `pr-ai-review-loop` through the Skill tool, acting as delegator: open `needs-triage` issues for matters requiring the user and remove their related changes from this PR (leave entries in the lessons library with issue numbers), while converging the remaining changes normally. Route follow-ups under escalation.md. After convergence, enable auto-merge with `gh pr merge --auto --squash`.

## Failure and stopping

Stop in the following cases and report the step, reason, and current state. Close out according to the case:

- The reviewed-through marker is missing, unparseable, or its SHA is not an ancestor of `origin/main`; `gh` authentication or API access fails; candidate collection is incomplete; or another step cannot be completed before section 6 item 1's commit: discard the branch and worktree, do not push or create a PR. Keep the marker unchanged so the next sweep retries the same window.
- The gate keeps failing, or `pr-ai-review-loop` hard-stops with unresolved matters: push the branch and create a draft PR if none exists. Do not enable auto-merge. Comment the blocker on the PR for takeover in section 1 of the next sweep.

The marker advances only when the sweep PR merges; leave unfinished windows for later sweeps.
