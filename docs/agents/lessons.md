# Recording and promoting lessons

[`docs/LESSONS.md`](../LESSONS.md) is the lesson library: record mistakes that warrant improving the environment, then promote recurring patterns when they reach the threshold. Both coding and process problems qualify; do not prejudge the destination while recording them.

Do not read the lesson library during implementation or review. Its sole writer is the [`lessons-sweep`](../../.agents/skills/lessons-sweep/SKILL.md) sweep; users may also modify it directly by manually invoking `/retro`. Other agents submit candidates only through the closing self-check below.

## Closing self-check

Every task that will result in a PR performs one closing self-check, including interactive sessions and small fixes. In AFK batches, implementer, stage-reviewer, fixer, and review-looper each perform their own check.

Ask: did this work include a mistake that warrants improving the environment? Any one of these qualifies:

- **Escaped to main**: post-merge fix, revert, or main CI failure.
- **Data, security, or real spending**: judge actual consequences: whether the mistake ultimately loses or damages user data, crosses a security boundary, or incurs real charges for an unintended model call, for example a trial run actually calling a model. This counts even if caught inside the PR. ArcReel's cost records and estimates are reference values; incorrect recording alone does not count unless it ultimately causes real spending.
- **Mechanically decidable**: lint, type checks, a script, or tests could detect this class of mistake, but existing checks did not. This counts even if caught inside the PR; the destination is a new check.
- **High rework cost**: discarding written code, or more than two rounds of back-and-forth changes to the same problem.

If none applies, it is not a lesson. Common non-examples: a judgment-based mistake caught in PR review and fixed once (review is working); problems reported by existing checks or standards; rejected review findings; wording-only changes.

For qualifying mistakes, after creating the PR, post one comment on it. Its first line is `<!-- lesson-candidate -->`; follow it with an entry in the [entry format](#条目格式), adding `根因：<…>` before `没拦住` in the occurrence record. Do not resubmit a candidate already posted on that PR.

If the problem originated in an earlier PR, still comment on the current PR and use the counting unit in “Counting and promotion.”

AFK roles without their own PR (implementer, and stage-reviewer or fixer before the stage PR exists) put the candidate in their handoff using the same format. The team-lead posts the combined candidates on the stage PR.

<a id="条目格式"></a>

## Entry format

The Chinese field labels below are the existing lesson-record format:

```markdown
### <one-sentence rule, phrased positively>
模式：<when this happens and what it looks like>
代价：<consequences>（严重度：常规 / 数据·安全·真实花费）
判定方式：机械（拟用 <tool>）/ 需要判断
- #3101 · 2026-10-13 · 审查发现：<one-sentence observation>；没拦住：<reason>
- #3139 · 2026-10-14 · 合并后修复：<one-sentence observation>；没拦住：<…>
```

- Derive occurrence counts and the last occurrence date from the records; do not add separate fields.
- Rules, patterns, and consequences describe lasting practices and outcomes. Put potentially stale details such as SHAs, line numbers, and file paths only in occurrence records.
- State how the issue was discovered, such as self-check, review, post-merge fix, revert, or main CI failure.
- `没拦住` takes one of three reasons: no corresponding rule; a rule existed but was not followed; the check was not connected. If an existing check or standard caught the mistake inside the PR, write `无，已被 <check-or-rule> 拦下`; promotion review first checks whether that interception is sufficient.

## Grouping

- Group by failure pattern. Grouping by code location scatters the same pattern and prevents it from reaching its threshold.
- Groups use level-two headings; entries use level-three headings. Do not predefine categories: use the closest group, or temporarily use `未分组` until at least two similar entries justify a group. Groups may be merged or split.
- The final `已晋升` section records moved entries, their destinations, and their occurrence records.

## Information boundary

This repository is public. Record only technical facts (PR and commit numbers, observations), without customer, channel, or commercial information. Do not record lessons involving private implementations here.

## Counting and promotion

- **Counting unit**: distinct PRs or issues. Repetitions within one PR count once. A single incident has one occurrence record; use the introducing PR, or the discovery PR if the former is unknown. Combine multiple sources about the same incident; a fixing PR may be added as evidence.
- **Promotion threshold**: two mechanically decidable occurrences become a check; three judgment-based occurrences become a rule; one data/security/real-spending occurrence triggers promotion review.
- **Destination**: a check, standard, skill, navigation pointer, or architecture document, judged by “Maintenance” in [`CODING_STANDARDS.md`](../../CODING_STANDARDS.md). If a rule existed but was not followed, prose is insufficient; prioritize a check or an architectural solution. Entries failing admission criteria remain in the library and continue counting. Append `评审未晋升 · <日期> · <一行理由>` (not an occurrence); reconsider only after a new record follows it. Put cross-domain patterns in `docs/standards/general.md`.
- **Expiry**: delete entries whose last occurrence was over 30 days ago; git history is the archive. Entries referencing an open issue and the `已晋升` section do not expire.

## Retiring standards

- Remove a rule if a check now enforces it or the architecture makes the mistake impossible.
- A rule is a removal candidate if no review comment cited it in the most recent 30 merged PRs touching its applicable paths. Decide with the admission counterfactual test. Count from the latest of: the lesson library's first reviewed-through (`486a901`, 2026-10-19), merge of the PR introducing the rule, or merge of the last PR retaining it. Do not decide until 30 matching PRs exist after that point. Review at most five candidates per sweep.
- When demoting a rule with incident history to the lesson library, include known occurrence records from `已晋升` and append `降级 · <日期> · 自 <规范文件 + 规则标题>`. Expiry starts from that line; renewed promotion counts only later occurrences, with earlier ones retained as context.
