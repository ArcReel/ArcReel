# 本地审查与建 PR 契约（第二阶段）

你审查实现阶段交付的 worktree，交付一个已 push、已建 PR 的分支。

输入变量（来自 spawn prompt）：issue 号、worktree 路径、分支名、lead 名、handoff 路径。

## 步骤

1. 用 EnterWorktree 的 `path` 接管实现阶段交付的 worktree；读 handoff 的「实现」段，不以其自报划定审查范围；`gh issue view <N>` 读验收标准与正文
2. 运行 `/code-review origin/main`，按 `/receiving-code-review` 的纪律评估两轴报告并就地修复（Spec 轴 findings：小缺口就地补齐，接近重做规模的请示 lead）；其请示场景与无法就地修复的架构级疑虑均 SendMessage 请示 lead
3. 修复后重新运行项目质量门（口径同实现契约）
4. main 已前进时，rebase 到最新 main 并重新验证
5. push 分支并建 PR：正文含 `Closes #<N>` 与验证说明，标题遵循项目 PR 规范

## 交付与退役

退役前按 [handoff.md](handoff.md) 追加「本地审查」段；超范围发现只记入其 follow-up 候选，不自行立项。SendMessage 向 lead 汇报：PR 号、审查发现与修复概要。lead 确认后退役。
