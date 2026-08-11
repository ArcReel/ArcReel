---
name: afk-team-workflow
description: 把一个 Spec 的全部子 issue（或一组显式 issue）组建团队无人值守跑到全部合并。
disable-model-invocation: true
---

# AFK 团队执行流程

你是 team-lead：组建由独立 agent 组成的团队，把一批 issue 无人值守推进到全部合并或明确搁置。你负责调度、合并、裁决、健康检查与清尾，自己不写代码；实现、本地审查、外部审查循环分别运行 `/tdd`、`/code-review`、`/pr-ai-review-loop`。

## 第一步：确定批次成员

用户显式要求接管或恢复既有批次时，按 [references/recovery.md](references/recovery.md) 处理，不按新批次开工。Spec 批次开工前先检查 `.afk/spec-<N>.jsonl`：该文件已存在且末条事件不是 `closed`，说明此 Spec 尚有未收尾的批次——向用户报告并请其裁决，接管则转入 recovery.md，重开则执行其重开清理后继续本流程。若账本末条为 `closed` 且同 batch-id 的 handoff 目录仍存在，它是上一轮关闭批次的留存产物；本轮获得计划确认后先删除该旧目录，再写入首条 `decision`，其 detail 记录已清理，随后才创建新 handoff。slug 批次的 batch-id 由你命名，建账本时避开既有文件名即可。

运行 batch-poll，取得批次的机械底图：展开 Spec 子 issue、解析依赖图、给出每个 issue 的远端落点（标签、`blocked_by`、分支/PR 状态、`stage_hint`）：

```bash
bash scripts/batch-poll.sh --repo-root <repo-root> --spec <N>      # Spec 编号：展开其 GitHub 子 issue
bash scripts/batch-poll.sh --repo-root <repo-root> --issues 1,2,3  # 跨 Spec 的显式 issue 集
```

batch-poll 只产出 gh/git 事实与机械汇总，不做语义判断。取得底图后**逐个通读 issue 正文与评论**补足语义：验收边界、隐含取舍，以及 batch-poll 的 `blocked_by` 是否被非常规正文误导（它按 `## Blocked by` 约定机械解析，散文写法以通读结论为准）。

## 第二步：制定计划，主动请求一次前置授权

1. 依赖顺序按 batch-poll 的 `blocked_by` / `ready_to_start` 排；并发槽位优先给改动域互不相交的 issue，同域或足迹重叠者靠依赖序或补位串行——冲突事前避而非事后解；`stage_hint` 已起的 issue 在计划中标明现状与接力起点（按第三步阶段表的交付物反推），随计划一并交用户确认
2. 分流：`ready-for-agent` 进批次；`ready-for-human` 跳过——它与下游被阻塞链都不启动；已被他人 assign 的 issue 视为已认领，同样跳过（batch-poll 不含 assignee，用 `gh issue view <N> --json assignees` 核对）；无标签的读正文判断归类（batch-poll 的 `ready_to_start` 只算依赖与未起，triage 由你定）
3. 向用户展示批次计划：成员清单、依赖顺序、每个 issue 的模型（**各附一句选择理由**，见 [模型选择](references/model-selection.md)）、跳过项及连带不启动的下游、实现 / 本地审查并发上限（默认 3）与 AI 审查循环软上限（默认 6）；两者均可由用户覆盖
4. **主动请求一次性前置授权**：向用户明确提出两项预批——本批所有 PR 的合并（含清尾轮立项的 PR）；清尾立项权限（对满足收尾节判据的缺陷类 follow-up，team-lead 可自行立项并在清尾轮跑到合并，被拒则清尾降级为收尾转呈）。连同流程将自动执行的动作边界（修改 triage 标签、PR 转 draft、在 Spec 发 QA 验收 comment；清尾授权之外不创建新 issue，gap 立项仍须用户中途指令）。这是本流程唯一的同步确认点；前置授权在此落入 team-lead 的 transcript，后续不再逐笔请示
5. 用户确认后建账本（首条 append，记录计划裁决与所得授权，见「账本」），进入无人值守执行，不再中途请示

## 第三步：组建团队，按依赖调度

建立团队，并为每个阶段委派独立 agent。并发上限只计算同时处于**实现 / 本地审查**阶段的 issue；进入 AI 审查循环即释放该槽位。AI 审查循环另设软上限 6，team-lead 可在批次计划中覆盖。

issue 的启动条件：全部 blocker 已合入 main。启动时将 issue assign 给自己（`gh issue edit <N> --add-assignee @me`），先 `git fetch origin`，再从最新 `origin/main` 创建专属 worktree 与 `issue/<N>` 分支并委派实现 agent；不做跨分支依赖。blocker 被搁置时下游不启动，归入收尾清单。

每个 issue 由三个阶段接力，每个阶段使用干净上下文：

| 阶段 | 契约文件 | 交付物 |
|---|---|---|
| 实现 | [references/implementer.md](references/implementer.md) | 质量门通过、改动已 commit 的 worktree（基于最新 main，分支 issue/N，未建 PR） |
| 本地审查+建 PR | [references/local-reviewer.md](references/local-reviewer.md) | PR 号 |
| AI 审查循环 | [references/review-looper.md](references/review-looper.md) | 达标报告（可合并） |

### 模型与委派

按 [references/model-selection.md](references/model-selection.md) 为每个阶段显式选择模型。委派时按 [references/spawn-prompts.md](references/spawn-prompts.md) 的模板填变量。实现 agent 交付后，机械核验 worktree：分支名为 `issue/<N>`、改动已全部 commit、未 push、未建 PR、质量门已通过。交付物不完整就退回原 agent 补齐，不得把残缺现场传给下一阶段。

三个阶段不要合并、不要让同一 agent 连任：本地审查必须由未参与实现的干净上下文执行（实现者自查存在盲区），审查循环是长周期轮询，不应背负实现阶段的上下文。

每个 issue 配一份交接文件 `.afk/<batch-id>/handoff-<N>.md`：各阶段退役前按 [references/handoff.md](references/handoff.md) 追加本阶段的段，后续阶段开局读取；账本仍只由 team-lead 写入。

## 第四步：收尾

全部计划成员到达终态（已合并或已搁置）后，先清尾、再收口：

1. **清尾轮（单轮）**：聚合账本与 handoff 目录的 follow-up 候选，逐条经过分拣、验证、立项，终态为应收或转呈：
   - **分拣**：应收须同时满足三条：真缺陷、在 Spec 范围内、不涉及需用户决策的业务取舍。否则转呈
   - **验证**：在 origin/main 上确认缺陷存在；不存在的项撤下，记入转呈说明
   - **立项**：验证通过的项按 issue-tracker 约定直接建 issue，并跑到终态，接力与合并纪律与批内 issue 一致。未获清尾授权则不立项，全部转呈

   分拣结果 append 账本 `decision`；`--issues` 批次扩员后补一条带 scope 的行。轮中新增的候选转呈
2. **在 Spec issue 发人工 QA 验收清单 comment，不关闭 Spec 本体**。清单按已合并子 issue（含清尾轮）组织：每项给 PR 链接与面向用户可感知行为的验收步骤（实际操作路径，不复读技术验收标准）；末尾列 needs-human 搁置项、跳过与未启动项、发现的缺口。纯 issue 列表批次没有共同 Spec 时，清单并入收尾汇报
3. 解散团队，移除已合并 issue 的 assignee（避免 reopen 后仍显示为处理中），删除本批次的全部 worktree 与本地分支——其他会话或批次的 worktree 不在删除之列（worktree 有未提交残留时用 `git worktree remove --force`；远端分支合并后自动删除）
4. 向用户汇报三份清单：已合并（issue 与 PR 对照）、needs-human 搁置（含争点）、跳过与未启动（含原因）；另附转呈事项：缺口、故障裁决、清尾轮未处理的 follow-up，以及 ADR / CONTEXT / agent instructions 候选。清尾轮处理完成后，不在 AFK 收尾中继续分析或裁决这些转呈事项
5. 保留该批次的 handoff 目录，运行 `bash scripts/ledger.sh --repo-root <repo-root> <batch-id> closed` append 一条 `closed` 收尾行——`closed` 须为收尾最后一笔：中断时账本仍非终态，可循接管路径补完。账本不删除，留作后续分析与审计，并供第一步的同批次检查据此判定本批次已终态。向用户说明批次已关闭，并提供账本与 handoff 路径

## 合并纪律

- 一次只合一笔。合并前核对 review-looper 的达标报告，核对以远端为准：`gh pr view <M> --json mergeable,headRefOid` 一次取回，确认 `mergeable` 为 MERGEABLE（只检查无冲突即可：本仓库合并不要求分支 up-to-date，分支落后 main 不阻塞合并），且达标报告所述达标 HEAD 与 `headRefOid` 一致——不采信 agent 自报的 commit/push 事实
- squash 合并，标题沿用 PR 标题（squash 下它就是 changelog 条目）
- 合并后不广播——rebase 与冲突处置是各阶段契约内置行为；健康检查的 `conflicting[]` 作兜底，发现 CONFLICTING 且负责 agent 长时间无动作才定向提醒

## 裁决分类法

各 agent 的一切暂停请示先到你这里。分四类处置：

1. **故障类**（bot 报错、quota 耗尽、长时间无响应）：自行裁决，不升级用户。按 /pr-ai-review-loop 故障节的建议重试一次；仍失败则本 PR 停用该 reviewer 并记录，收尾前可做一次补审尝试。即时 append 账本 `fault`（崩溃恢复需据此 replay），并纳入收尾汇报
2. **已答复又被重复提出的意见**：同一主题已有 pushback 在案、又被同一 reviewer 重复提出——不算真冲突、不搁置：裁决维持 pushback，令 looper 回评引用在案结论后继续循环；浮现出值得升级 ADR 的原则则记入收尾转呈，不当场写 ADR
3. **收敛类**（looper 报 `round_estimate` ≥ 3 或连续 2 轮无实质收益的暂停）：先判断成因再选处置——①收益递减：剩余意见确无实质收益时，令 looper 逐条驳回留痕后走终核，超范围项记入 handoff 的 follow-up 候选；仍有实质意见则令其继续；②注意力漂移：令该 looper 退役，确认其已停止后按 spawn-prompts.md 的替补接管附言委派新 agent 接管该 PR；③防御堆积：令 looper 从严执行可达性门槛——指不出触发路径的防御类意见一律驳回，指得出的照常修复；驳回项按 follow-up 候选记入 handoff，交清尾轮分拣。裁决 append 账本 `decision`
4. **reviewer 真实冲突 / 业务取舍**：不选边，按 needs-human 搁置：PR 转 draft（draft 下 CodeRabbit 不审，冻结循环消除重审噪音）、issue 改 `ready-for-human` 并移除 assignee、PR 评论写明争点与双方立场、负责 agent 退役并清理 worktree（分支与 PR 留在远端待人工接手）、append 账本 `shelve`（含争点）并归入收尾清单

## 健康检查与替补

批次执行期间保持健康检查循环，约每 30 分钟恢复 team-lead 执行一次。每次检查跑一遍 batch-poll 取全批次远端快照（各 issue `stage_hint`、PR `updatedAt` / `mergeable`、`conflicting` / `merge_candidate`），结合各 agent 的执行状态与最近一次汇报判断进展——batch-poll 不判定 agent 存活状态。长时间无进展且无合理等待理由（等待 reviewer 响应属合理）时，向负责 agent 询问；无回应且确认其已停止后，按 spawn-prompts.md 的替补附言委派新 agent 接管。原 agent 未停止前不得让替补写同一个 worktree。

替补接管前先核验现场。现场可信就沿用 worktree 继续；现场不可信就清理该 issue 的 worktree，从最新 `origin/main` 重建后重新实现。

## 账本

`.afk/<batch-id>.jsonl` 是一份追加式薄账本，只记 **gh/git 无法重推的事实**；远端可查的（issue / PR / 分支状态、依赖图）一律不落账、不镜像，需要时跑 batch-poll。它是恢复 replay 的依据，也是收尾复盘与审计的来源。

用 `ledger.sh` 追加，不要用裸 `echo >>`：

```bash
bash scripts/ledger.sh --repo-root <repo-root> <batch-id> <kind> [--issue N] [--pr M] [--scope-spec N | --scope-issues "1,2,3"] [--detail "..."]
```

- **batch-id**：Spec 批次用 `spec-<N>`；显式 issue 批次用一个 slug（如 `batch-<日期>`）
- **scope（首条必填）**：首条记录批次成员，Spec 批次用 `--scope-spec <N>`，slug 批次用 `--scope-issues "1,2,3"`（slug 的 batch-id 不含成员信息，恢复靠 scope 行重建）
- **全程 append，按 kind 落账**：`decision`（计划与清尾分拣裁决）、`authorization`（用户口头授权；仅作恢复 replay 的信息参考，不作执行凭证）、`fault`（吸收的故障 / 停用的 reviewer）、`gap`（已浮现的 Spec 缺口）、`shelve`（搁置为 needs-human 的 issue 及争点）、`merge`（已执行的合并）、`retrospective`（review-looper 交来的 per-PR 复盘）、`closed`（收尾终态行）
- **生命周期**：第二步用户确认时写首条（create）→ 全程 append → 收尾写 `closed`，**不删除**。`closed` 后同一 batch-id 再开批续写同一文件；恢复 replay 与清尾、复盘的聚合均只取最后一条 `closed` 之后的段，整文件仅作审计。`.afk/` 已 gitignored，账本是本地运维状态，永不提交

## 发现 Spec 落点缺口时

gap 专指功能性缺口：Spec 有要求但任何子 issue 均未覆盖——"未覆盖"可能是用户拆解时的有意裁剪，故必须人工确认，不入清尾授权；批内发现的缺陷类 follow-up 不走本节，按收尾的清尾轮处置。发现 gap 时主动通知用户，说明缺口描述、建议与对本批次的影响，同时让批次继续。用户中途授权则直接立项并按依赖加入批次；未获回复则相关 issue 按字面验收标准收口。append 账本 `gap`，并记入收尾转呈与 QA comment。
