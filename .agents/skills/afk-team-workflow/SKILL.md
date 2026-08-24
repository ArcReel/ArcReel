---
name: afk-team-workflow
description: 把一个 Spec 的全部子 issue（或一组显式 issue）组建团队无人值守跑到全部合并或明确暂停。
disable-model-invocation: true
---

# AFK 团队执行流程

你是 team-lead：把一个 Spec 的子 issue 或一组显式 issue 无人值守推进到全部合并或明确暂停。你负责计划、调度、集成与裁决；不实现 issue，只处理 stage 集成修复与 rebase 冲突。开工后持续运行到批次终态；中途不把调度问题升级给用户，真实业务取舍例外。

## 1. 计划批次

1. 生成唯一 `batch-id`：Spec 批次用 `spec-<N>-<UTC YYYYMMDD-HHMMSS>-<6 位随机十六进制>`，显式 issue 批次用同格式的简短 slug。若 `.afk/` 已有同一范围且未 `closed` 的账本，暂停并让用户选择接管或重开；两者均先读 [recovery.md](references/recovery.md)。
2. 按批次运行 `scripts/batch-poll.sh --repo-root <repo-root> --spec <N>` 或 `--issues <N,...>`，然后逐个通读 issue 正文与评论，得到真实的验收边界、canonical dependency graph、triage 与认领状态。只有 `OPEN` issue 可进入 stage 与 PR `Closes` 清单；其中 `ready-for-agent`、无他人认领且 blockers 已完成的 issue 进入 frontier，无标签时按语义裁决。`ready-for-human` 及其被阻塞下游不进入 frontier。
3. 将依赖图划成**最少的、可独立审查和合入的交付 stage**；小批次保持单 stage。按 [model-selection.md](references/model-selection.md) 为各角色选模型。
4. 向用户展示 stage、依赖、模型理由、跳过项及下游影响，并一次性请求：全部 stage PR 的 rebase merge 授权；最终清尾轮中对符合范围的真缺陷自行立 issue 的授权。未授权的清尾候选只转呈。
5. 用 `scripts/ledger.sh` 创建薄账本，记录 scope、计划裁决与授权；账本只记 Git / GitHub 无法重推的事实。

## 2. 执行 task graph

组建团队并按 [spawn-prompts.md](references/spawn-prompts.md) 委派。`HERDR_ENV=1` 时先读 [herdr-teammate.md](references/herdr-teammate.md)；否则使用当前 harness 的原生团队能力。

严格串行执行各 stage：

1. 从最新 `origin/main` 创建 `afk/<batch-id>/stage-<K>` 与专属 worktree，由 team-lead 独占并 push。
2. **slot-aware dispatch**：从依赖已满足且改动面可安全并发的 frontier 按当前可用 agent slots 认领并委派，slot 释放后继续。每个 issue 使用独立 worktree：implementer 按 [implementer.md](references/implementer.md) 交付后，由未参与实现的 local-reviewer 复用该 worktree，按 [local-reviewer.md](references/local-reviewer.md) 审查并交付一个 issue commit。不同 issue 的接力可自然重叠。**watchdog**：回收无进展 agent；确认停止后收回其写权与 slot，废弃未集成 handoff，从 remote truth 重新委派。
3. team-lead 在 stage worktree 串行 cherry-pick 已审查的 issue commits 并 push。冲突时 abort，由原 local-reviewer 基于最新 stage branch 解决、验证并重新交付。带 `Refs #<N>` 的 commit 出现在远程 stage branch 后，该 issue 才算完成并可解锁新 frontier。
4. 最后一个 stage 先聚合全批 handoff 的 follow-up：只处理经验证存在、属于批次范围且无需业务取舍的真缺陷；清尾 issue 创建后立即以 `membership` 记账并沿用同一接力，其余转呈。全部 issues 集成后收敛 **green HEAD**：stage worktree 与 remote HEAD 一致、干净且累计质量门通过；质量门产生的改动提交为 integration-fix 并 push。持续、不可用或无法修复的质量门故障记为 `fault`，暂停并询问用户。达标后创建 ready PR，用 `Closes #<N>` 覆盖本 stage issues。Spec 批次另用 `Refs #<Spec>` 引用 Spec，不自动关闭它。将 stage worktree 与 branch 的独占写权交给新 agent，按 [review-looper.md](references/review-looper.md) 收敛整个 stage diff；交接期间 team-lead 不写该 worktree 或 branch。收回写权后核对达标 HEAD 等于当前 `headRefOid` 且 `mergeable=MERGEABLE`，以该 `headRefOid` 为 expected-head 执行 rebase merge；不匹配则重入审查循环。下一 stage 从最新 `origin/main` 开始。

## 3. 暂停边界

实现或审查暴露真实业务取舍，或发现 Spec 要求没有 issue 覆盖时，暂停受影响事项及其下游并询问用户。**quiesce first**：停止受影响 agents，废弃未集成 handoff 并释放 slots；stage 写权已交出时，先停止 review-looper 写入并连同 worktree、branch、remote HEAD 与 handoff 收回。然后为已有 issue 移除 `ready-for-agent`、添加 `ready-for-human`，记录原因，并将暂停范围移出当前 stage。其余 frontier 继续执行。用户决定继续时恢复标签并重新编排；决定保留暂停时，仅当相关 commit 已进入 stage branch 才重建 stage，排除该 issue 及其下游；已有 PR 同步更新 `Closes` 清单。重建后重新运行累计质量门与审查循环。

可吸收的运行故障、reviewer 重复噪声与无需业务选择的技术裁决由 team-lead 处理并记账；阻断 **green HEAD** 且无法自行恢复的故障按上文暂停。

## 4. 收尾

在 Spec issue 发布按已合并 stage 组织的人工 QA 清单，列出 PR、用户可感知的验收路径、暂停/跳过项与转呈事项；显式 issue 批次则并入收尾汇报。移除本批添加的 assignee，清理本批的 agents、worktrees、本地 branches 与 Herdr workspace，最后 append `closed` 账本行。
