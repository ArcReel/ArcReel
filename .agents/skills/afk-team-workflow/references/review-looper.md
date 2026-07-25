# AI 审查循环契约（第三阶段）

你负责把 PR 推进到全部 AI reviewer 通过的可合并状态。

输入变量（来自 spawn prompt）：PR 号、issue 号、worktree 路径、lead 名、handoff 路径。

## 执行

先用 EnterWorktree 的 `path` 接管该 worktree——修复要在此工作树 push；读 handoff 的「实现」段（环境备案）与「本地审查」段（跳过项及理由，作为 pushback 依据）；若为替补接管，另读已追加的「审查循环」段以继承前任的 pushback 与故障记录，避免重复处理已驳回意见。随后按下列纪律驱动循环：

1. 用 Skill 工具调用 /pr-ai-review-loop，按其全部纪律执行，每轮动作后安排下一次唤醒
2. **请示重定向**：其中"暂停询问用户"的场景一律改为 SendMessage 请示 lead，按裁决继续；等待裁决期间保持唤醒监控 PR 动态
3. **rebase**：只在两种时机做——随下次修复 push 顺带完成（每次 push 触发全体 reviewer 重审一轮，合并也不要求分支 up-to-date，不为 main 前进单独 rebase），或每轮 poll 自检发现 CONFLICTING 时立即解冲突：rebase 到最新 main，按功能意图保留本 PR 的全部改动

## 交付与退役

目标状态终核通过后，先按 [handoff.md](handoff.md) 追加「审查循环」段；超范围发现只记入其 follow-up 候选，不自行立项。随后 SendMessage 向 lead 汇报达标结论、达标 HEAD（commit SHA）与轮数概要——复盘候选不直接呈用户，由 lead 在收尾时聚合统一呈用户。等待 lead 执行合并，确认合并完成后退役。
