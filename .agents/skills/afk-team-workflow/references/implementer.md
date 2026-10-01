# Implementer 契约

你实现一个 issue，向 team-lead 交付一个可集成的 commit。

输入：issue、repo root、stage branch、issue branch、handoff 绝对路径。

1. 通读 issue 正文与评论。验收标准是范围边界；与代码现实冲突或涉及业务取舍时请示 team-lead。
2. fetch 远程，从 stage branch 最新提交创建 `issue/<N>` 与专属 worktree，向 team-lead 回报路径。此后所有代码读写都在该 worktree，git 命令一律写 `git -C <worktree 绝对路径>`；服务端口与数据目录与其他 worktree 隔离。
3. 按 issue 实现；可行处按 TDD 推进，使用 Skill 工具调用 `tdd`。运行改动范围对应的项目质量门；前端质量门用 `pnpm check --maxWorkers=2`，多个 implementer 同时以 vitest 默认 worker 数运行时，机器负载会高到用例超时。
4. 将全部改动整理为一个 conventional commit：标题描述用户可感知变化，commit body 带 `Refs #<N>`，formatter 等产生的改动一并纳入，工作树干净。
5. 按 [handoff.md](handoff.md) 追加「实现」段，向 team-lead 回报 commit SHA 与验证结果。保留 worktree，不 push，不建 PR。
6. team-lead 回报 cherry-pick 冲突时，fetch 远程并将 issue branch rebase 到最新 stage branch，按功能意图解决冲突、重跑受影响的质量门，把质量门产生的改动纳入该 issue commit 并确认工作树干净，再次交付。team-lead 确认集成后退役，由 team-lead 清理 worktree 与本地 issue branch。
