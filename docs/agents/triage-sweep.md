# Unattended triage

Daily sweeps run `.agents/skills/triage/SKILL.md` using this document. Move every item to its next state, forwarding only judgment calls to maintainers. These rules replace the skill's interactive “recommend and wait for maintainer instructions” steps. Manual `/triage` invocation retains the skill's normal interaction.

## Conventions

- **Comments**: first line is the skill's required AI disclosure; second line is `<!-- triage-sweep -->`. Reuse the existing comment when the conclusion is unchanged.
- **State labels**: identify the next actor: `needs-triage` for a maintainer decision, `needs-info` for reporter/author information or revisions, `ready-for-agent` for agent implementation, `ready-for-human` for maintainer implementation or merge. Keep one state label per item, replacing the old one on transition. Items with multiple state labels go only in the sweep summary for maintainers to resolve. Every inspected item also gets category `bug` or `enhancement`.
- **External PRs**: `author_association` is neither `OWNER`, `MEMBER`, nor `COLLABORATOR`, and the author is not a bot.
- **Direction**: external PRs fixing reproducible bugs or documentation need no direction decision. Others, such as features, providers, endpoints, refactors, or behavior changes, need maintainer judgment.
- **Handoff**: apply `needs-triage` and comment with recommended state (`ready-for-agent`, `ready-for-human`, `needs-info`, or `wontfix`), reasoning, reproduction/validation results, and related `.out-of-scope/` entries. For external PRs also state whether direction is acceptable and takeover worthwhile: the problem has real value and amendment effort is clearly less than rewriting. `needs-triage` is the maintainer decision queue; replacing it with another state records the decision.

## 1. Select items

Handle each item using the first matching row.

| Item | Handling |
| --- | --- |
| Maintainer replaced `needs-triage` with another state, without a corresponding sweep comment yet | Step 5 |
| Issue without a state label | Step 2 |
| External PR without a state label | Step 3 |
| Issue or external PR with `needs-info` | Step 4 |
| External `ready-for-agent` PR already given revision requests, or PR under sweep takeover | Step 5, “Takeover” |
| External `merge-ready` PR | Invalidation rules under “Recommend merging” in step 3 |

Completion: list all items and the handling assigned to each.

## 2. Issues

Gather context using steps 1 and 3 of the skill's “Triage a specific issue or PR”: read the complete body/comments, check existing implementation and `.out-of-scope/`, and reproduce bugs using reported steps. Record why reproduction is impossible when applicable. Then take the first matching outcome:

1. Requested behavior already exists: identify implementation in a comment, apply `wontfix`, and close.
2. Insufficient information or reproduction fails with provided steps: post Triage Notes using the skill's `needs-info` template and apply `needs-info`.
3. Otherwise: hand off.

## 3. External PRs

Proceed in order, stopping at the step producing an outcome:

1. **Closure check**: when clearly covered by `.out-of-scope/`, or linked to a `ready-for-agent` issue (reason in `CONTRIBUTING.md`), comment with the entry/reason, apply `wontfix`, and close.
2. **CLA**: proceed if `license/cla` is success. If missing on the latest commit, visit `https://cla-assistant.io/check/ArcReel/ArcReel?pullRequest=<PR 号>` to trigger rechecking, then read status again. Still missing is not treated as unsigned: proceed and put the link in the summary. If unsigned, post one signing reminder per PR and apply `needs-info`.
3. **Checks**: gather context and validate the diff using skill steps 1 and 3, then review it with `code-review`. The fixed point is the PR's base branch; the spec is its linked issue or, absent that, its body. Already implemented requests close under step 2 outcome 1; insufficient information goes to `needs-info` under step 2 outcome 2.
4. **Route**:
   - Direction needs judgment: hand off. Do not list specific `code-review` revisions yet; step 5 sends those after the maintainer decides.
   - No direction decision needed and every “Recommend merging” condition holds: apply `ready-for-human` and `merge-ready`; comment with the recommendation and evidence.
   - No direction decision needed but `code-review` or validation finds needed changes: post revision requests to the author and apply `needs-info`.
   - Other PRs needing no direction decision: hand off, explaining unmet merge conditions.

### Recommend merging

Conditions:

- No direction decision is needed.
- `license/cla`, `ci-required`, and `codeql-required` are all success.
- `code-review` and validation find no needed changes.
- No sensitive paths: pricing/billing (`lib/config/`, `lib/billing/`), catalog/presets (`lib/catalog/`, `packages/arcreel-catalog-core/`), interface copy (`lib/i18n/`, `frontend/src/i18n/`), prompts (`lib/prompts/`, `agent_runtime_profile/`), DB/project migrations (`alembic/`, `lib/project/project_migrations/`), CI (`.github/`), dependency manifests or lockfiles.
- PR is not a draft.
- Its `main` purpose is not promoting commercial services (see `CONTRIBUTING.md`).

`merge-ready` means the sweep reviewed and validated the PR; maintainers perform the merge. Remove `merge-ready` on new commits or failing required checks and restart at step 3 outcome 3.

## 4. Waiting for the author

Handle `needs-info` items by the awaited matter:

- **CLA signing**: if signed, remove `needs-info` and continue at step 3 outcome 3. After more than 14 days with `needs-info` and no signature, regardless of replies, explain that signing allows reopening and close.
- **Information or revisions**: if the reporter/author replies, commits, or edits the body after the last sweep comment, remove `needs-info`. Issues return to step 2; external PRs restart at step 3 outcome 3. After more than 14 days without such activity, hand off external PRs worth taking over; otherwise explain that updates allow reopening and close.
- **The awaited matter is no longer required here**: remove `needs-info`; issues return to step 2 and external PRs restart at step 3 outcome 3.

## 5. Execute maintainer decisions

| Label | Issue | External PR |
| --- | --- | --- |
| `ready-for-agent` | Post an agent brief using the skill's AGENT-BRIEF.md | If direction is accepted and takeover worthwhile, send revisions and explain maintainer takeover after seven days without response |
| `ready-for-human` | Explain why implementation cannot be delegated, using the skill | No action |
| `needs-info` | Post Triage Notes and follow step 4 | If direction is accepted without takeover, send revisions and follow step 4 |
| `wontfix` | Close with the reason | Close with the reason |

Use `code-review` results for the latest commit when sending revisions. Closure reasons come from the maintainer's comment; without one, use the prior sweep recommendation, citing applicable `.out-of-scope/` entries. Put new rejection reasons in the summary for maintainers to decide whether to record them in `.out-of-scope/`.

### Takeover

For a `ready-for-agent` external PR, the sweep delegates takeover to a subagent after seven days without new commits since revision requests or the author's last reply, whichever is later. If new commits arrive within that period, the author continues and review restarts at step 3 outcome 3.

If maintainer edits are allowed, append commits to the original PR branch. Otherwise create a PR from latest main, retain `Co-authored-by` attribution, link it in the original PR, and close the original. After amendment, run `pr-ai-review-loop`; on convergence mark `ready-for-human`, then evaluate `merge-ready` conditions. The sensitive-path restriction does not apply to taken-over PRs.

## 6. Sweep summary

Completion: every item listed in step 1 is in a state required by a rule here.

Output the closing summary in Chinese: autonomously handled items/actions, conflicting state labels, new `needs-triage` handoffs, executed maintainer decisions, takeover progress, PRs missing CLA status and their recheck links, and proposed new `.out-of-scope/` rejection reasons.
