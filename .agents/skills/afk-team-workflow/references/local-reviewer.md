# 本地审查与建 PR 契约（第二阶段）

你接手实现阶段交付的 worktree，以未参与实现的视角做一遍完整审查——实现者的自查存在盲区，独立的审查上下文正是为此设置。交付一个已 push、已建 PR 的分支。

输入变量（来自 spawn prompt）：issue 号、worktree 路径、分支名、lead 名。

## 步骤

1. 进入 worktree；`gh issue view <N>` 读验收标准，对照改动逐条核对覆盖情况
2. 运行 /code-review --fix 修复发现的问题；无法就地修复的架构级疑虑 SendMessage 请示 lead
3. 修复后重新运行项目质量门（口径同实现契约）
4. main 已前进时，rebase 到最新 main 并重新验证
5. push 分支并建 PR：正文含 `Closes #<N>` 与验证说明，标题遵循项目 PR 规范
6. PR 保持正常状态，不用 draft——draft 下 CodeRabbit 不自动审查，会阻塞下一阶段

## 交付与退役

SendMessage 向 lead 汇报：PR 号、审查发现与修复概要。lead 确认后退役。
