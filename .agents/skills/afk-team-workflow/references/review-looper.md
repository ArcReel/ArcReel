# AI 审查循环契约（第三阶段）

你负责把 PR 推进到全部 AI reviewer 通过的可合并状态：调用 /pr-ai-review-loop 执行循环，并把其中的人工请示重定向给 lead。

输入变量（来自 spawn prompt）：PR 号、issue 号、worktree 路径、lead 名、handoff 路径。

## 执行

先用 EnterWorktree 的 `path` 接管该 worktree——修复要在此工作树 push；读 handoff 的「实现」段（环境备案）与「本地审查」段（跳过项及理由，作为 pushback 依据）；若为替补接管，另读已追加的「审查循环」段以继承前任的 pushback 与故障记录，避免重复处理已驳回意见。随后按下列纪律驱动循环：

1. 用 Skill 工具调用 /pr-ai-review-loop，按其全部纪律执行：poll、触发、收集评论转交 /receiving-code-review、ScheduleWakeup 控制轮询节奏。每轮动作后必须安排下一次唤醒
2. **请示重定向**：skill 内所有"暂停询问用户"的场景（故障、收敛兜底、reviewer 冲突、业务取舍）一律 SendMessage 请示 lead，按裁决继续。等待裁决期间保持 ScheduleWakeup 监控 PR 动态
3. **rebase 时机**：main 前进不需要任何即时动作，rebase 随下次修复 push 一并完成——每次 push 都会触发全部 reviewer 重审一轮，减少 push 次数就是减少重审轮数。达标后若再无修复要 push，也不必为落后 main 单独 rebase：合并不要求分支 up-to-date，无冲突即可由 lead 直接合并。每轮 poll 自检 mergeable，PR 进入 CONFLICTING 时立即解冲突：以最新 main 为基线 rebase，本 PR 的全部改动按功能意图重新保留并调整到与 main 兼容——不要在冲突区简单取 main 一侧、丢弃本 PR 的改动

## 交付与退役

目标状态终核通过后，先按 [handoff.md](handoff.md) 追加「审查循环」段：pushback 在案清单、/pr-ai-review-loop 退出时按其 references/retrospective.md 产出的复盘候选全文（过程总结 + ADR / CONTEXT.md / CLAUDE.md / follow-up 四类候选，多数情况为空）、故障记录；超范围发现只记入其 follow-up 候选，不自行立项。随后 SendMessage 向 lead 汇报达标结论、达标 HEAD（commit SHA）、轮数概要与复盘摘要——复盘候选不直接呈用户，由 lead 在收尾时聚合全部 per-PR 复盘统一呈用户。等待 lead 执行合并，确认合并完成后退役。
