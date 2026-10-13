---
name: afk-team-workflow
description: 把一个 Spec 的全部子 issue 或一批零散 issue 组建团队无人值守跑到全部合并或明确暂停。
disable-model-invocation: true
---

# AFK 团队执行流程

你是 team-lead：把一个批次无人值守推进到全部合并或明确暂停。你负责计划、调度、集成与裁决，代码全部委派给 teammates。开工后持续运行到批次终态：调度问题自行裁决，只把真实业务取舍转呈用户。

## 1. 计划批次

两种批次只在下表各项上不同，其余流程一致：

| | Spec 批次 | 零散批次 |
|---|---|---|
| 范围 | `batch-poll.sh --spec <N>` | 用户给出编号时 `--issues <N,...>`；要求处理非 Spec 的 `ready-for-agent` issue 时 `--ready` |
| stage 划分 | 最少的、可独立审查和合入的 stage；小批次保持单 stage | 每个 stage 的 issues 能共用一个带 scope 的 conventional PR 标题；在依赖顺序下取最少 stage |
| 验收来源 | Spec 与本 stage issues | 本 stage issues |
| PR 引用 | `Closes #<N>`，另用 `Refs #<Spec>` 引用 Spec，不自动关闭它 | `Closes #<N>` |
| 清尾 issue | 按 [issue-tracker 约定](../../../docs/agents/issue-tracker.md) 挂接父 Spec | 独立创建；用户授权时指定了 Spec 则挂接该 Spec |
| 人工 QA 清单 | 发布在 Spec issue | 发布为最后一个 stage PR 的评论 |
| 账本 scope | `--scope-spec <N>` | `--scope-issues <N,...>` |

1. 运行 `scripts/batch-poll.sh --repo-root <repo-root>`，范围参数按上表。随后逐个通读 issue 正文与评论，得到真实的验收边界、canonical dependency graph、triage 与认领状态。`--ready` 只做机械筛选，调研、评估等不产出代码的 issue 由你剔除。
2. 确定 frontier：只有 `OPEN` issue 可进入 stage 与 PR `Closes` 清单；其中带 `ready-for-agent`、无他人认领且 blockers 已完成的进入 frontier。issue 没有 triage 标签时按语义裁决；`ready-for-human` issue 及其被阻塞的下游留在 frontier 之外。
3. 将本批范围与 `.afk/` 中未 `closed` 账本的 scope、用户告知的并行批次对照。范围相同时暂停并让用户选择接管或重开，两者均先读 [recovery.md](references/recovery.md)；部分重叠时列入第 5 步的展示。
4. 按上表将依赖图划成交付 stage。按 [model-selection.md](references/model-selection.md) 为各角色选模型与 effort。
5. 向用户展示 stage、依赖、模型理由、跳过项及下游影响；`--ready` 批次另列入选与剔除两份清单，由用户确认或删减。同时一次性请求两项授权：全部 stage PR 的 rebase merge；最终清尾轮中对符合范围的真缺陷自行立 issue。未获授权的清尾候选只转呈。
6. 用 `scripts/ledger.sh --new` 创建薄账本，记录 scope、计划裁决与授权；它生成并输出本批 `batch-id`。账本只记 Git / GitHub 无法重推的事实。

## 2. 执行 task graph

组建团队并按 [spawn-prompts.md](references/spawn-prompts.md) 委派。`HERDR_ENV=1` 时先读 [herdr-teammate.md](references/herdr-teammate.md)；否则使用当前 harness 的原生团队能力。

严格串行执行各 stage：

1. 从最新 `origin/main` 创建 `afk/<batch-id>/stage-<K>` 与专属 worktree，并 push stage branch。
2. 将依赖已满足且改动面可安全并发的 frontier 认领并委派。每个 issue 使用独立 worktree，implementer 按 [implementer.md](references/implementer.md) 交付一个 issue commit。
3. 在 stage worktree 串行 cherry-pick 交付的 issue commits 并 push。冲突时 abort，由原 implementer 基于最新 stage branch 解决、验证并重新交付。带 `Refs #<N>` 的 commit 出现在远程 stage branch 后，该 issue 才算完成并可解锁新 frontier；先核对，再用单独的命令清理 issue worktree 与本地 branch。
4. 最后一个 stage 先聚合全批 handoff 的 follow-up：只处理经验证存在、属于批次范围且无需业务取舍的真缺陷，清尾 issue 按上表挂接后沿用同一接力；其余转呈。
5. 全部 issues 集成后，由未参与本 stage 实现的 stage-reviewer 按 [stage-reviewer.md](references/stage-reviewer.md) 审查整个 stage diff 并交付 **green HEAD**。
6. 创建 draft PR，按上表用 `Closes #<N>` 覆盖本 stage issues，再启动 review-looper 收敛 AI 审查与 commit history。
7. review-looper 回报达标 HEAD 后，核对它等于当前 `headRefOid` 且 `mergeable=MERGEABLE`，以该 `headRefOid` 为 expected-head 执行 rebase merge；不匹配则重入审查循环。下一 stage 从最新 `origin/main` 开始。

## 3. 暂停边界

实现或审查暴露真实业务取舍，或发现 Spec 要求没有 issue 覆盖（Spec gap）时，暂停受影响事项及其下游并询问用户，其余 frontier 继续执行：

1. **quiesce first**：停止受影响 agents 并废弃未集成 handoff；stage-reviewer 或 review-looper 运行时，先停止它并核对 worktree、branch、remote HEAD 与 handoff。
2. 为已有 issue 移除 `ready-for-agent`、添加 `ready-for-human`，记录原因，并将暂停范围移出当前 stage。
3. 用户决定继续时：已有 issue 恢复标签；Spec gap 先创建并挂为 sub-issue，再重新编排。
4. 用户决定保留暂停时：仅当相关 commit 已进入 stage branch 才重建 stage，排除该 issue 及其下游；已有 PR 同步更新 `Closes` 清单。重建后重新运行集成审查与审查循环。

可吸收的运行故障、reviewer 重复噪声与无需业务选择的技术裁决由你处理并记账；阻断 **green HEAD** 且无法自行恢复的故障按上文暂停。review-looper 硬停汇报后，由你裁决未决事项并交给新 looper 接力：给出延长的 `rounds`，裁定驳回的意见随委派交它回复。

## 4. 收尾

按上表发布人工 QA 清单，按已合并 stage 组织，列出 PR、用户可感知的验收路径、暂停或跳过项与转呈事项。移除已认领 issue 的 assignee，清理本批的 agents、worktrees、本地 branches 与 Herdr workspace，最后 append `closed` 账本行。

收尾汇报中给出 `batch-id`、QA 清单链接、账本与 handoff 路径，并保留这些批次记录。用户此后对转呈事项的裁决仍以 `decision` 追加到本批账本。
