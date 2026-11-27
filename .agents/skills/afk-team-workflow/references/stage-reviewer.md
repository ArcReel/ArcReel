# Stage reviewer 契约

你负责在 stage 全部 issue 集成后审查整个 stage diff，完成修复后向 team-lead 交付 **green HEAD**，供其创建 stage PR。在你交付前，你是远端 stage branch 的唯一写者。

输入：stage branch、stage worktree、Spec（仅 Spec 批次）、本 stage issues、各 issue 实际实现 Harness、batch handoff 目录、stage handoff 绝对路径。stage 重建后另给原 integration-fix commit 的 SHA。

1. 确认 stage worktree、branch 与远端最新提交一致。通读 Spec（若有）、本 stage 所有 issue 正文与评论及其 handoff。git 命令一律使用 `git -C <stage worktree 绝对路径>`。
2. 以 `origin/main` 为固定点、输入的 Spec 与本 stage issues 为范围，调用 [code-review](../../code-review/SKILL.md)；两个并行审查子代理按 [model-routing](../../model-routing/SKILL.md) 的 Review 路由启动（按输入的实际实现 Harness 查表，只读产出 findings）。跨 issue 改动触及共享契约、状态写入或检查触发时，核对消费端回读或实际运行的检查。Spec 有未被任何 issue 覆盖的要求时，作为 Spec gap 请示 team-lead。
3. 以实际 diff 和验收边界裁决 findings，断言行为缺陷前按生产调用方的真实用法复现。修复成立的 findings，并结清各 issue handoff 中以 stage 集成为移除条件的临时实现；涉及复杂根因修复时，可按 [model-routing](../../model-routing/SKILL.md) 的 `debug-fix` 委派子代理在 `stage worktree` 内修改源码并跑相关测试，将未提交改动交回给你，验收连贯性后由你统一运行闸门、提交并 push。给了原 integration-fix commit 时，逐处读它的改动：对本 stage 现有 issue 仍成立的修复重新做进来，只服务于已排除 issue 的不带入，取舍在 handoff 各留一行。接近重做，或按 [escalation.md](../../../../docs/agents/escalation.md) 需要找用户时请示 team-lead，其余自行决定并在 handoff 留一行理由。
4. 用 `uv run python scripts/gate.py --changed --full` 跑本 stage diff 触及的全部域的完整闸门。闸门卡住（同一失败特征第 2 次出现）时记为 `fault` 并上报 team-lead。
5. 全部修复提交为 issue commits 之后的一个 integration-fix commit，commit message 按 [`CONTRIBUTING.md` 提交规范](../../../../CONTRIBUTING.md)；闸门通过后 push 一次。没有修复时不产生这个 commit。
6. 达到 **green HEAD** 且工作树干净后，按 [lessons.md](../../../../docs/agents/lessons.md)「收尾自查」自查，按 [handoff.md](handoff.md) 追加「集成审查」段（仅对已采纳修复的行为缺陷附最短证据指针），向 team-lead 回报 HEAD SHA 与验证结果并停止。
