# Reviewer protocols

This document covers reviewer triggers, coverage, finding retrieval, and feedback. Script flag fields are navigation clues, not code judgments; classification and convergence follow [SKILL.md](../SKILL.md). Completed reviews may still have findings, and a pass marker cannot override undisposed findings.

## Identity and triggering

| Reviewer | Snapshot login | REST login | Automatic review | Manual trigger |
|---|---|---|---|---|
| CodeRabbit | `coderabbitai` | `coderabbitai[bot]` | PR opened, subsequent push | `@coderabbitai resume` / `@coderabbitai review` |
| Codex | `chatgpt-codex-connector` | `chatgpt-codex-connector[bot]` | PR opened, continued review after fix pushes | `@codex review`, fallback only when initial review has not started |
| CodeQL | None | `github-advanced-security[bot]` | Analysis after every push | None |

Send each trigger type only once per HEAD, placing the command alone at the start of a comment; reconcile using `own_trigger_comments` and `last_push_at`. Use CodeRabbit's `walkthrough.updated_at` for trigger timing, as described below. Pure metrics bots such as Codecov do not participate in this loop.

CodeRabbit and Codex automatically follow pushes; reconcile current HEAD using their respective completion signals. If only replying without changing HEAD, confirmed coverage remains valid.

## Reading

Locate new bodies via `is_new` and batch-read them with `query.sh ... details <id>...`. Severity determines ordering; classification follows “Engineering judgment” in SKILL.md. Thanks and confirmed withdrawals are not blockers. If flags conflict with previews or bodies, read the full text.

Use `query.sh ... history` / `unacked` to find omissions, not as a queue requiring modifications for every unacknowledged item. Snapshots omit non-bot inline replies. To verify recorded dispositions, use `gh api 'repos/<owner>/<repo>/pulls/<pr>/comments?per_page=100&page=<N>'` from page 1 until a page contains fewer than 100 entries, linking by `in_reply_to_id`. Do not use `--paginate`: next-page links it follows use numeric-ID paths, rejected by REST-only sessions. Read top-level disposition comments from `repos/<owner>/<repo>/issues/<pr>/comments`.

## Write back dispositions

Record a disposition for every finding. For defects, the fix itself is the record and the bot confirms against the new HEAD; no reply is needed. For others, reply in the original inline thread with classification and evidence. For findings without an inline ID, reply at PR top level and identify the source ID. See “Feedback” in each reviewer section for official feedback channels.

## CodeRabbit

**Trigger.** If `walkthrough.is_paused == true` and no resume was sent after its `updated_at`, send `@coderabbitai resume`. If `walkthrough.is_review_skipped == true` (a skipped-review notice for drafts, bot authors, etc.) and no review was sent after its `updated_at`, send `@coderabbitai review`. Otherwise wait for automatic review. Silence after pause, rate limiting, or skipping is not completion.

**Coverage complete.** `reviewed_current_head == true`, `is_in_progress == false`, and `is_paused == false`. Walkthrough updates during rate limiting or skipped reviews do not count as reviews; the script excludes them using `is_rate_limited` and `is_review_skipped`.

**Finding entry points.** Read new inline comments and review bodies. `has_outside_diff` indicates outside-diff findings in the body, which have no inline ID. `is_ok` and `actionable_count` are bot summaries and cannot replace those bodies. When incremental review returns `Already reviewed`, both values may remain from the previous round; use this round's body and actual dispositions. Convergence requires all substantive findings disposed, not `actionable_count` reaching zero.

**Feedback.** CodeRabbit learns from thread conversations, and learnings affect later reviews throughout the repository. Keep replies in inline threads without `@coderabbitai`, avoiding repository-wide learnings from one PR's judgment. If the same noise recurs and `docs/standards/` does not cover it, propose a `lesson-candidate` under [lessons.md](../../../../docs/agents/lessons.md) for inclusion in standards; both CodeRabbit and Codex read review rules from standards.

## Codex

**Trigger.** Wait if the PR is less than 5 minutes old. After 5 minutes, send one `@codex review` only if there is still no review, inline, reaction, or `has_started`. Once a start signal appears or review participation begins, wait for automatic continued review after subsequent pushes; handle timeouts under [faults.md](faults.md).

**Coverage complete.** Any of these completion signals appears for the current HEAD: a review containing `### 💡 Codex Review`; an empty-body `COMMENTED` review with no new inline comments this round; a top-level pass comment with `has_pass_marker` and `reviewed_current_head == true`; or a `+1` reaction confirmed to belong to this HEAD (new signals are marked `is_new`). After a new push, `eyes` means rereview is underway; the previous HEAD's `+1` cannot be reused.

**Finding entry points.** Read non-ack inline comments and review bodies with `has_body_finding == true`. Prioritize P0/P1.

**Feedback.** Each inline finding ends with `Useful? React with 👍 / 👎.`; reactions are Codex's usefulness-feedback channel. After classification, react to the inline: use `+1` for defects and preexisting issues (valid findings), and `-1` for YAGNI, nitpicks, and false positives. Reactions do not replace replies. Review-body findings have no reaction entry point; reply only at PR top level.

```bash
gh api -X POST repos/<owner>/<repo>/pulls/comments/<id>/reactions -f content=-1
```

## CodeQL security exit threshold

CodeQL does not read inline replies; analysis updates alerts after fixes. Unfixed alerts may not produce another comment. Check `security_alerts.open_introduced` every time, rather than only new comments this round.

Exit requires both:

- `codeql_checks.all_ok == true`: analysis for current HEAD is complete and successful. `total == 0`, pending, and failed are not passes.
- `security_alerts.available == true`, with `open_introduced` empty or containing only verified, recorded false positives. This list excludes preexisting base alerts; `N new alerts` in check-run titles cannot replace it.

Verify new alerts under “Evidence” in SKILL.md. For confirmed false positives, record the taint-chain endpoint and conclusion by alert number in a top-level PR comment; this adds them to the recorded list. Refer inconclusive evidence to the delegator. Only the user dismisses alerts; list alerts awaiting dismissal in the exit report.

**Previously authorized false-positive family.** For `py/path-injection`, if the taint chain ends at the path returned by `ProjectManager.get_project_path()` itself and its internal `safe_join` protection of `project_name` still holds, verify and record directly. A tainted, unvalidated `filename` or similar concatenated after that return value is outside this exception. Record the alert number and actual taint endpoint; reuse existing conclusions when their premises remain unchanged.

Reconcile the recorded list by alert number from top-level PR comments; `query.sh ... history` does not include these manual top-level explanations. Insufficient permissions may also return 404, so this does not establish the repository lacks integration. If analysis remains 0 throughout, alerts are unavailable, and that bot has never commented, this only suggests missing integration: obtain delegator confirmation before skipping and report it as unchecked. See [faults.md](faults.md) for other analysis or permission failures.

## Live queries

Prefer query.sh. If direct snapshot queries are necessary, first validate field paths with a known nonempty query. `gh` occasionally truncates output while returning 0; confirm you have the complete original before writing back remote content such as PR bodies.
