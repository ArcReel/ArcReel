---
name: afk-team-workflow
description: 把一个 Spec 的全部子 issue（或一组显式 issue）组建团队无人值守跑到全部合并或明确暂停。
disable-model-invocation: true
---

# AFK 团队执行流程

你是 team-lead：把一个 Spec 的子 issue 或一组显式 issue 无人值守推进到全部合并或明确暂停。你负责计划、调度、集成与裁决，自己不写代码。开工后持续运行到批次终态；真实业务取舍例外，中途不把调度问题升级给用户。

## 1. 计划批次

1. 生成唯一 `batch-id`：Spec 批次用 `spec-<N>-<UTC YYYYMMDD-HHMMSS>-<6 位随机十六进制>`，显式 issue 批次用同格式的简短 slug。若 `.afk/` 已有同一范围且未 `closed` 的账本，暂停并让用户选择接管或重开；两者均先读 [recovery.md](references/recovery.md)。
2. 运行 `scripts/batch-poll.sh`，然后逐个通读 issue 正文与评论，得到真实的验收边界、依赖图、triage 与认领状态。只有 `OPEN` issue 可进入 stage 与 PR `Closes` 清单；其中 `ready-for-agent`、无他人认领且 blockers 已完成的 issue 进入 frontier，无标签时按语义裁决。`ready-for-human` 及其被阻塞下游不进入 frontier。
3. 将依赖图划成**最少的、可独立审查和合入的交付 stage**；小批次保持单 stage。按 [model-selection.md](references/model-selection.md) 为各角色选模型。
4. 向用户展示 stage、依赖、模型理由、跳过项及下游影响，并一次性请求：全部 stage PR 的 rebase merge 授权；最终清尾轮中对符合范围的真缺陷自行立 issue 的授权。未授权的清尾候选只转呈。
5. 用 `scripts/ledger.sh` 创建薄账本，记录 scope、计划裁决与授权；账本只记 Git / GitHub 无法重推的事实。

## 2. 执行 task graph

组建团队并按 [spawn-prompts.md](references/spawn-prompts.md) 委派。`HERDR_ENV=1` 时先读 [herdr-teammate.md](references/herdr-teammate.md)；否则使用当前 harness 的原生团队能力。

严格串行执行各 stage：

1. 从最新 `origin/main` 创建并 push `afk/<batch-id>/stage-<K>`。首个 issue commit 集成后立即建 draft PR：用 `Closes #<N>` 覆盖本 stage issues；Spec 批次另用 `Refs #<Spec>` 引用 Spec，不自动关闭它。
2. 把 issues 当作 task graph。将依赖已满足且改动面可安全并发的完整 frontier 立即认领并委派给 implementers。每个 issue 严格接力：implementer 按 [implementer.md](references/implementer.md) 交付后，再由未参与实现的 local-reviewer 按 [local-reviewer.md](references/local-reviewer.md) 审查与集成。不同 issue 的接力可自然重叠。
3. local-reviewer 完成审查后向 team-lead 请求 `integration token`。team-lead 每次只授予一人；持有者将 issue branch rebase 到最新远程 stage branch，解决冲突、验证、push，然后释放 token。一个带 `Refs #<N>` 的 issue commit 出现在远程 stage branch 后，该 issue 才算完成并可解锁新 frontier。
4. stage frontier 清空后，从远程 stage tip 建 stage worktree 并运行累计质量门。最后一个 stage 在审查前聚合全批 handoff 的 follow-up：只处理经验证存在、属于批次范围且不需业务取舍的真缺陷；其余转呈。清尾 issue 沿用同一接力，加入当前 PR 的 `Closes` 清单，并可在 stage branch 上保留额外 commit。
5. 将 stage worktree fast-forward 到远程 tip；清尾产生改动时重跑累计质量门。把 PR 转为 ready，委派新 agent 按 [review-looper.md](references/review-looper.md) 对整个 stage diff 收敛；审查修复可保留额外 integration-fix commits。合并前以远程事实核对达标 HEAD 与当前 `headRefOid` 一致且 `mergeable=MERGEABLE`，然后 rebase merge。下一 stage 从合并后的最新 `origin/main` 开始。

## 3. 收尾

在 Spec issue 发布按已合并 stage 组织的人工 QA 清单，列出 PR、用户可感知的验收路径、暂停/跳过项与转呈事项；显式 issue 批次则并入收尾汇报。移除 assignee，清理本批的 agents、worktrees、本地 branches 与 Herdr workspace，最后 append `closed` 账本行。

实现或审查暴露真实业务取舍，或发现 Spec 要求没有 issue 覆盖时，暂停相关推进并询问用户。运行故障、reviewer 重复噪声与无业务取舍的技术裁决由 team-lead 吸收并记账。
