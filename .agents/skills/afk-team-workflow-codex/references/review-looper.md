# AI 审查循环契约（Codex 第三阶段）

输入：`repo_root`、issue 号、PR 号、绝对 worktree、lead canonical task 名、绝对 handoff。

所有命令显式在 worktree 中运行。先读实现/本地审查 handoff；替补还要读已有“审查循环”段。然后使用共享 `$pr-ai-review-loop` 及其原始文件，不能复制或改写 reviewer 业务规则：

- `<repo_root>/.agents/skills/pr-ai-review-loop/SKILL.md`
- `<repo_root>/.agents/skills/pr-ai-review-loop/references/reviewers.md`
- `<repo_root>/.agents/skills/pr-ai-review-loop/references/retrospective.md`
- 该 skill 的 `poll.sh`、`query.sh`、`classify_commits.sh`

## Codex 运行时适配

共享 skill 中的 reviewer 门槛、actionable、触发、故障与收敛规则保持原样，只替换以下运行时原语：

1. 始终在传入的绝对 worktree 工作，结束时保留它给 lead。
2. 共享 skill 进入下一轮延迟时，由本 teammate 自己调用 `collaboration.wait_agent(timeout_ms=60000)`：60 秒等 1 次，120 秒连续等 2 次，180 秒连续等 3 次。每次等待提前收到 lead 消息就立即处理；PR 轮询只使用这一等待机制。
3. 所有“询问用户”重定向为 `collaboration.send_message` 给 lead，包含事实、原文和可选裁决。等待裁决时继续按上条监控；运行中的回复由 lead `send_message`，若本 agent 已结束当前 turn，则 lead 用 `followup_task` 续同一 teammate。
4. 常规 `no_change`、reviewer 响应中和普通 idle 不汇报 lead。只在达标、故障、真实 reviewer 冲突或业务取舍时汇报。
5. main 前进时不单独 rebase；下次修复 push 一并 rebase。PR 进入 `CONFLICTING` 时立即按功能意图解决冲突并重验。

## 交付

目标状态终核通过后，在 handoff 追加“### 审查循环”段，内容严格按共享 retrospective 参考。向 lead 报告达标 HEAD、轮数、参审结果、pushback、故障和复盘摘要；等待 lead 核验并合并后结束。

完成条件：共享 skill 的全部目标门槛对当前远端 HEAD 成立，`unacked` 兜底已处理，handoff 与报告指向同一 HEAD。
