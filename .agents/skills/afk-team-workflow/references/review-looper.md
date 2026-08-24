# Stage AI 审查循环契约

你负责把一个 stage PR 推进到全部 AI reviewers 通过的可合并状态。

输入：PR、stage branch、stage worktree、本 stage issues、batch handoff 目录、stage handoff 绝对路径。

1. 确认 worktree 与远程 stage branch 最新提交一致，读 stage 内所有 issue 及其 handoff，以合并后的验收边界审查整个 stage diff。
2. 运行 `/pr-ai-review-loop`，采用其评论、CI、pushback、等待与终核纪律；以目标状态全部达成为终点。轮数、边际收益与重复主题只是给 team-lead 的诊断信号，其软收敛出口与故障询问均先上报 team-lead。
3. 普通审查修复以额外 conventional integration-fix commits 普通 push，保持 stage branch 追加式前进。`/pr-ai-review-loop` 需要 rebase 或 force-push 时停止写入并交回 team-lead；team-lead 将 stage rebase 到最新 `origin/main`，解决冲突、运行累计质量门，再用 `--force-with-lease` 更新远程。保留每个 issue 的单个 conventional commit 与 `Refs #<N>`，按 [handoff.md](handoff.md) 记录新旧 HEAD 后从新 HEAD 重启本循环。
4. reviewer 意见超出批次范围时回复说明边界，并记为 follow-up。意见涉及真实业务取舍时请示 team-lead；team-lead 暂停相关推进并询问用户。
5. 重复噪声引用已有 pushback；reviewer 故障重试一次后可停用该 reviewer，两者都写入 stage handoff。
6. 终核通过后按 [handoff.md](handoff.md) 追加「审查循环」段，向 team-lead 报告达标 HEAD 与轮数。等待 team-lead 合并后退役。
