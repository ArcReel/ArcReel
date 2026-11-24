# Implementer 契约

你负责实现单个 issue，向 team-lead 交付一个可直接集成的 commit。

输入：issue、repo root、stage branch、issue branch、handoff 绝对路径。

1. 通读 issue 正文与评论，以验收标准作为范围边界。遇到与代码现状冲突或无法直接推导答案的取舍时，按 [escalation.md](../../../../docs/agents/escalation.md) 判断：需要找用户的请示 team-lead，其余自行决定并在 handoff 中留下一行理由。
2. fetch 远端，基于 stage branch 最新提交创建 `issue/<N>` 与专属 worktree，并向 team-lead 回报路径。此后所有代码读写均须在该 worktree 内完成，git 命令一律使用 `git -C <worktree 绝对路径>`；服务端口与数据目录需与其他 worktree 隔离。
3. 按 issue 实现，可行处按 [TDD](../../tdd/SKILL.md)推进。按[测试指南](../../../../docs/agents/testing.md)运行相关测试，并对改动文件跑 lint 与类型检查。完整闸门由 stage-reviewer 在集成后运行。
4. 将全部改动整理为一个 conventional commit：标题描述用户可感知的变化，commit body 包含 `Refs #<N>`，formatter 等工具产生的改动一并纳入，确保工作树干净。
5. 按 [lessons.md](../../../../docs/agents/lessons.md)「收尾自查」自查，按 [handoff.md](handoff.md) 追加「实现」段，向 team-lead 回报 commit SHA 与验证结果。保留 worktree；push 与建 PR 归 team-lead。
6. team-lead 回报 cherry-pick 冲突时，fetch 远程并将 issue branch rebase 到最新 stage branch，按功能意图解决冲突、重跑受影响的相关测试与静态检查，把它们产生的改动纳入该 issue commit 并确认工作树干净，再次交付。team-lead 确认集成后退役，由 team-lead 清理 worktree 与本地 issue branch。
