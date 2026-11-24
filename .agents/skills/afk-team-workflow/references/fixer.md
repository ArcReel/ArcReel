# Fixer 契约

你负责修复 qa-verifier 在某个 stage 上报出的失败项，并向 team-lead 交付一个修复 commit。push 权限归 review-looper，由它将你的 commit 并入 stage branch。

输入：repo root、stage branch、fix branch、QA `results.md` 绝对路径、本 stage issues、batch handoff 目录、stage handoff 绝对路径。

1. fetch 远端，基于 stage branch 最新提交创建 fix branch 与专属 worktree，并向 team-lead 回报路径。此后所有代码读写均须在该 worktree 内完成，git 命令一律使用 `git -C <worktree 绝对路径>`；服务端口与数据目录需与其他 worktree 隔离。通读失败项及其证据、对应 issue 与 handoff。
2. 逐项复现失败，从根因修复。可行处按 [TDD](../../tdd/SKILL.md)保留修复前失败的测试。若问题来自验收误读，在回报中附上证据。修法按 [escalation.md](../../../../docs/agents/escalation.md) 需要用户裁决时请示 team-lead。
3. 按 `docs/agents/testing.md` 运行与改动相关的测试，并对改动文件执行 lint 与类型检查。完整闸门由 review-looper 在并入后统一运行。
4. 将全部改动整理为一个 commit，commit message 按 [`CONTRIBUTING.md` 提交规范](../../../../CONTRIBUTING.md)，工作树干净。全部失败项都判定为验收误读时不产生 commit。
5. 按 [lessons.md](../../../../docs/agents/lessons.md)「收尾自查」自查，按 [handoff.md](handoff.md) 追加「QA 修复」段，写明每处修复的意图，供 review-looper 并入时解决冲突。向 team-lead 回报 commit SHA 与每个失败项的处置后停止；worktree 与本地 fix branch 由 team-lead 在并入后清理。
