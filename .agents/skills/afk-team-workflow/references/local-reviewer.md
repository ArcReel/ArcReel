# 本地审查与建 PR 契约（第二阶段）

你审查实现阶段交付的 worktree，交付一个已 push、已建 PR 的分支。

输入变量（来自 spawn prompt）：issue 号、worktree 路径、分支名、lead 名、handoff 路径。

## 步骤

1. 用 EnterWorktree 的 `path` 接管实现阶段交付的 worktree；读 handoff 的「实现」段——读交接不损害独立审查，但实现者自查存在盲区，交接自报不划定审查范围；`gh issue view <N>` 读验收标准与正文，对照改动做规格核对，三问都要过：验收要求有无缺失或只完成一半；改动有无 issue 未要求的行为（scope creep）；已覆盖项的实现是否与要求相符（看似实现但实现得不对）。小缺口就地补齐，接近重做规模的请示 lead
2. 运行 /code-review high --fix 修复发现的问题——固定 high 档：最后一道本地审查取 recall 优先，误报有验证步与 --fix 取舍兜底，更高档位的边际召回交给外部审查循环；无法就地修复的架构级疑虑 SendMessage 请示 lead
3. 修复后重新运行项目质量门（口径同实现契约）
4. main 已前进时，rebase 到最新 main 并重新验证
5. push 分支并建 PR：正文含 `Closes #<N>` 与验证说明，标题遵循项目 PR 规范
6. PR 保持正常状态，不用 draft——draft 下 CodeRabbit 不自动审查，会阻塞下一阶段

## 交付与退役

退役前按 [handoff.md](handoff.md) 追加「本地审查」段；超范围发现只记入其 follow-up 候选，不自行立项。SendMessage 向 lead 汇报：PR 号、审查发现与修复概要。lead 确认后退役。
