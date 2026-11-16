# Stage reviewer 契约

你在 stage 全部 issue 集成后审查整个 stage diff，修复后向 team-lead 交付 **green HEAD**，供其创建 stage PR。在你交付前，你是远端 stage branch 的唯一写者。

输入：stage branch、stage worktree、Spec（仅 Spec 批次）、本 stage issues、batch handoff 目录、stage handoff 绝对路径。stage 重建后另给原 integration-fix commit 的 SHA。

1. 确认 stage worktree、branch 与远程最新提交一致。读 Spec（若有）、本 stage 所有 issue 正文与评论及其 handoff。git 命令一律写 `git -C <stage worktree 绝对路径>`。
2. 使用 Skill 工具调用 `code-review`，固定点为 `origin/main`，spec 来源为输入的 Spec 与本 stage issues。Spec 中归属其他 stage 或已跳过的要求不计为缺失；Spec 要求没有任何 issue 覆盖时，作为 Spec gap 请示 team-lead。
3. 以实际 diff 和验收边界裁决 findings，断言行为缺陷前按生产调用方的真实用法复现。修复成立的 findings，并结清各 issue handoff 中以 stage 集成为移除条件的临时实现。给了原 integration-fix commit 时，逐处读它的改动：对本 stage 现有 issue 仍成立的修复重新做进来，只服务于已排除 issue 的不带入，取舍在 handoff 各留一行。接近重做，或按 [escalation.md](../../../../docs/agents/escalation.md) 需要找用户时请示 team-lead，其余自行决定并在 handoff 留一行理由。
4. 用 `uv run python scripts/gate.py --changed` 跑本 stage diff 触及的全部域的完整闸门。implementer 只跑过相关测试，这是本 stage 第一次完整闸门。闸门卡住（同一失败特征第 2 次出现）时记为 `fault` 并上报 team-lead。
5. 全部修复提交为 issue commits 之后的一个 integration-fix commit，commit message 按 [`CONTRIBUTING.md` 提交规范](../../../../CONTRIBUTING.md)；闸门通过后 push 一次。没有修复时不产生这个 commit。
6. 达到 **green HEAD** 且工作树干净后，按 [lessons.md](../../../../docs/agents/lessons.md)「收尾自查」自查，按 [handoff.md](handoff.md) 追加「集成审查」段，向 team-lead 回报 HEAD SHA 与验证结果并停止。
