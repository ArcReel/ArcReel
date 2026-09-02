# Stage AI 审查循环契约

你负责把一个 stage PR 推进到 **green HEAD**、全部 AI reviewers 通过且可合并。

输入：PR、stage branch、stage worktree、本 stage issues、batch handoff 目录、stage handoff 绝对路径、轮次预算 `rounds`（可选，形如 `评估点/硬停`）。

1. 确认 stage worktree、branch 与远程最新提交一致，读 stage 内所有 issue 及其 handoff，以合并后的验收边界审查整个 stage diff，含并行 issue 间的重复实现与接缝收敛。git 命令一律写 `git -C <stage worktree 绝对路径>`。
2. 运行累计质量门，修复并以 integration-fix commit push；commit message 按 [`CONTRIBUTING.md` 提交规范](../../../../CONTRIBUTING.md)。持续失败时记为 `fault` 并上报 team-lead。达到 **green HEAD** 后将 PR 转为 ready。
3. 使用 Skill 工具调用 `pr-ai-review-loop`，采用其评论、CI、pushback、等待、轮次与终核纪律；委派方为 team-lead，轮次预算以输入的 `rounds` 为准（省略即该 skill 的默认值）。wait.sh 与质量门等长任务前台阻塞执行。断言行为缺陷前，按生产调用方的真实用法复现。普通修复以额外 integration-fix commits push。评估汇报、硬停汇报、业务取舍与故障询问均发给 team-lead；硬停后按 [handoff.md](handoff.md) 追加「审查循环」段并停止。
4. 收到 rebase 指令时，rebase 到最新 `origin/main`，解决冲突、重跑累计质量门，以 `--force-with-lease` push，并按 [handoff.md](handoff.md) 记录新旧 HEAD。保留每个 issue 的单个 conventional commit 与 `Refs #<N>`。
5. reviewer 意见超出批次范围时回复说明边界，并记为 follow-up。意见涉及真实业务取舍时请示 team-lead，收到裁决后回执确认；team-lead 按主流程的暂停边界持久化 issue 状态并阻断当前 stage 合并，直到用户裁决并完成对应恢复或重建。
6. 终核通过后，将本 stage 的所有非 issue commits 压成一个同规范的 integration-fix commit。确认压缩前后 tree 一致后，以 `--force-with-lease` push；新 HEAD 的 required checks 通过后，按 [handoff.md](handoff.md) 追加「审查循环」段，回报达标 HEAD 与轮数并停止。
