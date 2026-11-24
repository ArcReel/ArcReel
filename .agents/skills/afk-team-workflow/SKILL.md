---
name: afk-team-workflow
description: 把一个 Spec 的全部子 issue 或一批零散 issue 组建团队无人值守跑到全部合并或明确暂停。
disable-model-invocation: true
---

# AFK 团队执行流程

你是 team-lead：把一个批次无人值守推进到全部合并并验收，或明确暂停。你负责计划、调度、集成与裁决，代码与验收全部委派给 teammates。开工后持续运行到批次终态：调度问题自行裁决，是否找用户按 [escalation.md](../../../docs/agents/escalation.md) 判断。

## 1. 计划批次

两种批次只在下表各项上不同，其余流程一致：

| | Spec 批次 | 零散批次 |
|---|---|---|
| 范围 | `batch-poll.sh --spec <N>` | 用户给出编号时用 `--issues <N,...>`；要求处理非 Spec 的 `ready-for-agent` issue 时用 `--ready` |
| stage 划分 | 最少的、可独立审查和合入的 stage；小批次保持单 stage | 每个 stage 的 issues 能共用一个带 scope 的 conventional PR 标题；在依赖顺序下取最少 stage |
| 验收来源 | Spec 与本 stage issues | 本 stage issues |
| PR 引用 | `Closes #<N>`，另用 `Refs #<Spec>` 引用 Spec，不自动关闭它 | `Closes #<N>` |
| 清尾 issue | 按 [issue-tracker 约定](../../../docs/agents/issue-tracker.md) 挂接父 Spec | 独立创建 |
| 收尾报告 | 发布在 Spec issue 下 | 发布为最后一个 stage PR 的评论 |
| 账本 scope | `--scope-spec <N>` | `--scope-issues <N,...>` |

1. 运行 `scripts/batch-poll.sh --repo-root <repo-root>`，范围参数按上表。随后逐个通读 issue 正文与评论，得到真实的验收边界、canonical dependency graph、triage 与认领状态。`--ready` 只做机械筛选，调研、评估等不产出代码的 issue 由你剔除。
2. 确定 frontier。只接手开放、未被他人认领、且阻塞项已完成的 issue。已分流的 issue 须带 `ready-for-agent`。Spec 或点名批次中的无标签 issue，由 team-lead 按内容判断是否可交给 Agent。`--ready` 只选带 `ready-for-agent` 的 issue，筛选和人工剔除后为空就说明原因并结束。`ready-for-human` issue 及其被阻塞的下游不进入 frontier。PR 的 `Closes` 只列本 stage 实际处理的开放 issue。
3. 将本批范围与 `.afk/` 中未 `closed` 账本的 scope、用户告知的并行批次对照。范围相同时暂停并让用户选择接管或重开，两者均先读 [recovery.md](references/recovery.md)；部分重叠时列入第 5 步的展示。
4. 按上表将依赖图划成交付 stage。各角色启动前按 [model-routing](../model-routing/SKILL.md) 选模型与 effort，记录 Category 或路由与一句理由：stage-reviewer 走 Review 路由，qa-verifier 选 `deep-low`，两者都使用未参与本 stage 任何实现的干净上下文。
5. 向用户展示 stage、依赖、模型理由、跳过项及下游影响；`--ready` 批次另列入选与剔除两份清单，由用户确认或删减。同时请求两项授权：全部 stage PR 的 rebase merge，以及部署冒烟中真实生成的费用预算。
6. `--ready` 批次经用户删减后没有入选 issue，在会话中说明原因并结束。其余情况使用 `scripts/ledger.sh --new` 创建薄账本，记录 scope、计划裁决与授权信息；该脚本会生成并输出本批次的 `batch-id`。账本仅记录 Git / GitHub 无法重新推导的事实。

## 2. 执行 task graph

用当前环境的原生团队能力委派。任务消息附对应角色契约的绝对路径、契约列出的全部输入和必要背景，文件与 worktree 一律用绝对路径；角色复用与 worktree 创建以本流程和各角色契约为准。改派或接力前先确认原 agent 已停止。

各 stage 严格串行，前一个合并后下一个才从最新 `origin/main` 开始。每个 stage 依次经过集成、集成审查、PR 三个阶段，远端 stage branch 在每个阶段只有一个写者：

| 阶段 | 写者 | 结束条件 |
|---|---|---|
| 集成 | team-lead | 本 stage 的 issue commits 全部在远端 stage branch |
| 集成审查 | stage-reviewer | 交付 **green HEAD** |
| PR | review-looper | stage PR 合并 |

stage branch 的历史形状固定为：每个 issue 一个 commit，其后至多一个 integration-fix commit。集成审查与 PR 阶段的修复都并入这个 integration-fix commit 再 push，所以每次 push 的 HEAD 都可以直接合并，不为整理历史单独 push。

### 集成

1. 从最新 `origin/main` 创建 `afk/<batch-id>/stage-<K>` 与专属 worktree，并将 stage branch 推送到远端。
2. 认领并委派依赖已满足、改动面可并发的 issues。每个 issue 使用独立 worktree，implementer 按 [implementer.md](references/implementer.md) 交付一个 issue commit。交付确认前失效的实现，停掉前任并弃用未集成的 handoff，从远程 stage branch 的最新提交重新委派。
3. 在 stage worktree 串行 cherry-pick 交付的 issue commits 并 push。冲突时 abort，由原 implementer 基于最新 stage branch 解决、验证并重新交付。带 `Refs #<N>` 的 commit 出现在远程 stage branch 后，该 issue 才算完成并可解锁新 frontier；先核对，再用单独的命令清理 issue worktree 与本地 branch。
4. 最后一个 stage 先聚合全批 handoff 的 follow-up：经验证存在、属于批次范围且按 escalation.md 无需找用户的真缺陷，立清尾 issue 按第 1 节的表挂接后沿用同一接力；其余按 escalation.md 分流。清尾轮自己产生的新发现，无论是否在批次范围内，真缺陷都按 escalation.md「范围外的真缺陷」开 issue，其余按同节分流，随后即结束：不开启新一轮清尾，也不找用户。

### 集成审查

全部 issues 集成后，由 stage-reviewer 按 [stage-reviewer.md](references/stage-reviewer.md) 审查整个 stage diff 并交付 **green HEAD**；委派消息中明确授权其按 `code-review` 启动双轴并行子代理。

### PR

1. 收到 green HEAD 后同时做三件事，让 CI、AI 审查与验收一起开始：
   - 创建非 draft 的 stage PR：按第 1 节的表用 `Closes #<N>` 列出本 stage 全部 issues；本 stage 各 handoff 中自行裁决的事项及一行理由写进正文；教训候选发成 `lesson-candidate` 评论，格式见 [lessons.md](../../../docs/agents/lessons.md)。
   - 另建一个检出 green HEAD 的 QA worktree，在其中启动 qa-verifier，按 [qa-verifier.md](references/qa-verifier.md) 以全量模式验收。该 stage 的复验沿用同一个 qa-verifier。
   - 启动 review-looper，按 [review-looper.md](references/review-looper.md) 收敛 AI 审查。
2. qa-verifier 交付后，把 QA 结论以一条文字评论发在 PR 上，写明全量验收的 commit；此后每次复验都更新这条评论，并写明复验的 commit。再把结论通知 review-looper：
   - 没有失败项：告知 review-looper 本轮 QA 没有待并入的修复。
   - 有失败项：把本轮全部失败项交给一个新的 fixer 按 [fixer.md](references/fixer.md) 处理。
     - fixer 交付了 commit：转交 review-looper，它 push 后由 qa-verifier 在新 HEAD 上只复验这些项。
     - fixer 判定为验收误读、没有改代码的项：把依据交给 qa-verifier 复验，结论改为通过即结案，仍失败按卡住处理。全部失败项都是误读时没有 commit，同样告知 review-looper 本轮 QA 没有待并入的修复。

   复验仍有失败项时重复本步，直到没有失败项，或剩余失败项**卡住**（同一失败特征第 2 次出现）。
3. 卡住的项按性质分流：
   - **回归**（批次前已有的行为被破坏）：按第 3 节暂停该 issue 及其下游。
   - **新验收未达成**（已有行为完好）：该 issue 的 commit 留在 stage，从 PR 的 `Closes` 清单中移除，在 issue 上评论 QA 证据摘要，保持 open 与 `ready-for-agent`。
4. review-looper 回报达标 HEAD 后，逐项核对，全部成立才合并：
   - **审查收敛**：达标 HEAD 与 PR 当前的 `headRefOid` 一致。
   - **检查通过**：该 HEAD 的 required checks 全部通过，且 `mergeable=MERGEABLE`。
   - **QA 覆盖该 HEAD**：不存在未分流的失败项，且 `results.md` 中每项的目标 commit 均为该 HEAD。若 HEAD 因修复或 rebase 发生变化，未复验项仍记录旧 commit；此时需由 qa-verifier 以复验模式按新 HEAD 核对覆盖范围，若发现失败项则回到第 2 步。
   - **PR 记录是最终结论**：`Closes` 清单与 QA 结论评论都与最终结论一致。

   任一项不成立时处理后重新核对；HEAD 因此改变的，等 review-looper 再次回报达标。
5. 以该 `headRefOid` 为 expected-head 执行 rebase merge。

## 3. 暂停边界

实现、审查或验收暴露按 escalation.md 需要找用户的事项，或发现 Spec 要求没有 issue 覆盖（Spec gap）时，暂停受影响事项及其下游，按 escalation.md 的格式询问用户，其余 frontier 继续执行：

1. **quiesce first**：停止受影响 agents 并废弃未集成 handoff；stage-reviewer、qa-verifier、fixer 或 review-looper 运行时，先停止它并核对 worktree、branch、remote HEAD 与 handoff。
2. 为已有 issue 移除 `ready-for-agent`、添加 `ready-for-human`，记录原因，并将暂停范围移出当前 stage。
3. 用户决定继续时：已有 issue 恢复标签；Spec gap 先创建并挂为 sub-issue，再重新编排。
4. 用户决定保留暂停时：仅当相关 commit 已进入 stage branch 才重建 stage，排除该 issue 及其下游。重建由你执行，此时本 stage 没有其他写者：从最新 `origin/main` 重新 cherry-pick 保留的 issue commits，以 `--force-with-lease` push，已有 PR 同步更新 `Closes` 清单。原 integration-fix commit 不带入：它混有针对被排除 issue 的修复。重建后从集成审查重新开始，原 PR 沿用，QA 以全量模式重做；把原 integration-fix commit 的 SHA 交给 stage-reviewer，由它取回对保留 issue 仍成立的修复。

可吸收的运行故障、reviewer 重复噪声与无需找用户的裁决由你处理并记账；阻断 **green HEAD** 且无法自行恢复的故障按上文暂停。review-looper 硬停汇报后，由你裁决未决事项并交给新 looper 接力：给出延长的 `rounds`，裁定驳回的发现随委派交它回复。

## 4. 收尾

1. **部署冒烟**：全部 stage 合并后，取本批最后一个触发 `docker-edge` 构建的合并 commit 为目标，由新的 qa-verifier 以冒烟模式在部署实例上验收全批跨 stage 的主路径与各 stage 的「待部署验证」项，费用以授权预算为上限。冒烟失败项按 escalation.md「范围外的真缺陷」开 issue 并附证据摘要，不开启修复轮。本批没有合并触发镜像构建时跳过冒烟，待部署验证项转为无法验证。
2. **收尾报告**：按上表发布一条消息，按已合并 stage 组织，列出 PR、QA 与冒烟的结论计数、留 open 的 issue、暂停或跳过项。其后是人工清单：各 `results.md` 中当前结论仍为无法验证的项各附原因（待部署验证项以冒烟结论为准），以及需要用户裁决的事项，后者按 escalation.md「怎么问」的格式合成。两者都为空时省略人工清单。
3. 移除已认领 issue 的 assignee，停止本批所有 agents，清理本批创建的 worktrees、本地 branches 及其他运行资源，最后向账本追加 `closed` 行。

收尾汇报中给出 `batch-id`、收尾报告链接、账本、handoff 与证据目录路径，并保留这些批次记录。用户此后对这些事项的裁决仍以 `decision` 追加到本批账本。
