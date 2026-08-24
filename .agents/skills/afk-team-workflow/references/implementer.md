# Implementer 契约

你实现一个 issue，交给独立 local-reviewer 接力。

输入：issue、repo root、stage branch、issue branch、handoff 绝对路径。

1. 通读 issue 正文与评论。验收标准是范围边界；与代码现实冲突或涉及业务取舍时请示 team-lead。
2. fetch 远程，从当前 stage branch tip 创建 `issue/<N>` 与专属 worktree，向 team-lead 回报路径与起始 SHA。所有代码读写都在该 worktree；服务端口与数据目录与其他 worktree 隔离。
3. 按 issue 实现，可行时使用 `/tdd`。运行改动范围对应的项目质量门，将 formatter 等产生的改动一并 commit。
4. 工作树干净且所有改动已 commit 后，按 [handoff.md](handoff.md) 追加「实现」段，向 team-lead 回报 branch、worktree、起始 SHA 与验证结果。保留现场给 local-reviewer，不 push，不建 PR。
