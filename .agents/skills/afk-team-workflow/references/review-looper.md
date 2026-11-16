# Stage AI 审查循环契约

你负责把一个 stage PR 推进到 **green HEAD**、审查收敛且可合并；收敛以 `pr-ai-review-loop` 的完成条件为准，不以所有 bot 都给出通过措辞为准。PR 合并前，你是远端 stage branch 的唯一写者：QA 修复、审查修复与 rebase 都经你 push。

输入：PR、stage branch、stage worktree、本 stage issues、batch handoff 目录、stage handoff 绝对路径、轮次预算 `rounds`（可选，形如 `评估点/硬停`）。

## stage branch 的历史形状

每个 issue 一个带 `Refs #<N>` 的 conventional commit，其后至多一个 integration-fix commit。你的每次 push 都保持这个形状：本批修复并入 integration-fix commit（还没有时新建，commit message 按 [`CONTRIBUTING.md` 提交规范](../../../../CONTRIBUTING.md)），跑完整闸门，再以 `--force-with-lease` push 一次。完整闸门卡住（同一失败特征第 2 次出现）时记为 `fault` 并上报 team-lead。

## 循环

1. 确认 stage worktree、branch 与远程最新提交一致：首次启动时该提交是最新交付的 **green HEAD**，接力时是前任硬停汇报的 HEAD。读 stage 内所有 issue 及其 handoff，以 stage handoff 已有各段为已裁决起点。git 命令一律写 `git -C <stage worktree 绝对路径>`。
2. 使用 Skill 工具调用 `pr-ai-review-loop`，采用其工程判断、覆盖核对、等待、轮次与终核约定；委派方为 team-lead，轮次预算以输入的 `rounds` 为准（省略即该 skill 的默认值），修复按上文的历史形状 push。等待脚本与完整闸门等长任务前台阻塞执行。
3. 首次 push 前等三样到齐，合成一批处置：各参审 reviewer 对当前 HEAD 的首轮结果、required checks 的结果、team-lead 转达的 QA 结论。QA 与 CI、AI 审查同时进行；等待期间先完成已到发现的定性与本地修复。三样都没有需要改代码的事项时不 push。
4. team-lead 转交 fixer 的 commit 时，把它 cherry-pick 进 stage worktree 并入 integration-fix commit，与手头其他修复同批 push，再向 team-lead 回报新 HEAD 供 QA 复验。cherry-pick 冲突时由你解决：冲突的另一方是你手头的修复或已改写的 integration-fix commit，只有你的 worktree 同时有两边。按两处修复各自的意图合并，读 stage handoff 的「QA 修复」段取 fixer 的意图；无法判断时请示 team-lead。
5. 收到 rebase 指令时，rebase 到最新 `origin/main`，解决冲突、重跑完整闸门后 push，并按 [handoff.md](handoff.md) 记录新旧 HEAD。
6. reviewer 的发现超出批次范围时，按该 skill 定性；属于存量的，回复边界并记为 follow-up 候选。发现按 [escalation.md](../../../../docs/agents/escalation.md) 需要找用户时请示 team-lead，收到裁决后回执确认；team-lead 按主流程的暂停边界持久化 issue 状态并阻断当前 stage 合并，直到用户裁决并完成对应恢复或重建。评估汇报与故障询问同样发给 team-lead。
7. 终核通过后，按 [lessons.md](../../../../docs/agents/lessons.md)「收尾自查」自查，按 handoff.md 追加「审查循环」段，向 team-lead 回报达标 HEAD 与轮数，然后待命：team-lead 在合并前交来 fixer commit 或 rebase 指令时，按第 4、5 步处理并重新收敛、再次回报。
8. 达到硬停后，按该 skill 完成最后一批必要复审与终核：新发现都能以回复结案时，回复后进入第 7 步；需要改代码时，按 handoff.md 追加「审查循环」段并自查，向 team-lead 作硬停汇报并停止。
