# Skill translation audit exceptions

Only human-readable labels or prose inside inline spans were translated. Commands, paths, tool identifiers, field keys and marker syntax retain their structure. The coordinator reviewed these spans against their original clauses. They change human placeholders or instruction prose, not executable flags, IDs, marker keywords, or workflow decisions.

| File | Baseline span | Candidate span | Reason |
|---|---|---|---|
| afk-retrospective/SKILL.md | `.afk/reports/<batch-id>/<唯一文件名>.html` | `.afk/reports/<batch-id>/<unique-filename>.html` | Human filename placeholder; uniqueness and historical preservation explicitly retained. |
| lessons-sweep/SKILL.md | `gh pr view <PR 号> --json isDraft,autoMergeRequest,mergeStateStatus,statusCheckRollup` | `gh pr view <PR-number> --json isDraft,autoMergeRequest,mergeStateStatus,statusCheckRollup` | Placeholder only. |
| lessons-sweep/SKILL.md | `<!-- reviewed-through: <SHA> <日期> -->` | `<!-- reviewed-through: <SHA> <date> -->` | Date placeholder only; functional marker keyword unchanged. |
| lessons-sweep/SKILL.md | `git log <SHA>..<窗口终点> --first-parent` | `git log <SHA>..<window-endpoint> --first-parent` | Placeholder only; commit range and flags unchanged. |
| lessons-sweep/SKILL.md | `chore(lessons): <YYYY-MM-DD> 教训巡检` | `chore(lessons): <YYYY-MM-DD> lessons sweep` | Human commit title; conventional prefix/date retained. |
| afk-team-workflow/references/fixer.md | `git -C <stage worktree 绝对路径>` | `git -C <absolute-stage-worktree-path>` | Absolute-path placeholder only. |
| afk-team-workflow/references/implementer.md | `git -C <worktree 绝对路径>` | `git -C <absolute-worktree-path>` | Absolute-path placeholder only. |
| afk-team-workflow/references/review-looper.md | `评估点/硬停` | `evaluation-point/hard-stop` | Human round-budget label; slash and semantics retained. |
| afk-team-workflow/references/review-looper.md | `git -C <stage worktree 绝对路径>` | `git -C <absolute-stage-worktree-path>` | Absolute-path placeholder only. |
| afk-team-workflow/references/spawn-prompts.md | `开工先委派独立探索 agent 勘察。` | `First delegate reconnaissance to an independent exploration agent.` | Instruction prose, semantically unchanged. |
| afk-team-workflow/references/stage-reviewer.md | `git -C <stage worktree 绝对路径>` | `git -C <absolute-stage-worktree-path>` | Absolute-path placeholder only. |


The nested-backtick parser false positives in the skill README were fixed in `scripts/audit_translation.py` and protected by a regression test; they are not accepted exceptions.
