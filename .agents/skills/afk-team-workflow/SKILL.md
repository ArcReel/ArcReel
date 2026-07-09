---
name: afk-team-workflow
description: 把一个 Spec 的全部子 issue（或一组显式 issue）组建团队无人值守跑到全部合并。
disable-model-invocation: true
---

# AFK 团队执行流程

你是 lead：组建团队，把一批 issue 无人值守推进到全部合并或明确搁置。你负责调度、合并、裁决、健康检查与清尾，自己不写代码；实现、本地审查、外部审查循环、补立项分别交给 /tdd、/code-review、/pr-ai-review-loop、/to-tickets。

## 触发前先检查未完成批次

开工前检查 `.afk/` 是否存在**末条事件不是 `closed`** 的账本：`for f in .afk/*.jsonl; do [ -f "$f" ] || continue; tail -n1 "$f" | jq -e '.kind == "closed"' >/dev/null 2>&1 || echo "$f"; done`。若有，则上一会话的 lead 可能中途终止——读 [references/recovery.md](references/recovery.md) 按接管流程处理，不要当作全新批次直接覆盖。

## 第一步：确定批次成员

先跑 batch-poll 取批次的机械底图：展开 Spec 子 issue、解析依赖图、给出每个 issue 的远端落点（标签、`blocked_by`、分支/PR 状态、`stage_hint`）：

```bash
bash .agents/skills/afk-team-workflow/scripts/batch-poll.sh --spec <N>      # Spec 编号：展开其 GitHub 子 issue
bash .agents/skills/afk-team-workflow/scripts/batch-poll.sh --issues 1,2,3  # 跨 Spec 的显式 issue 集
```

batch-poll 只产出 gh/git 事实与机械汇总，不做语义判断。取得底图后**逐个通读 issue 正文与评论**补足语义：验收边界、隐含取舍，以及 batch-poll 的 `blocked_by` 是否被非常规正文误导（它按 `## Blocked by` 约定机械解析，散文写法以通读结论为准）。

## 第二步：制定计划，主动请求一次前置授权

1. 依赖顺序按 batch-poll 的 `blocked_by` / `ready_to_start` 排；并发槽位优先给改动域互不相交的 issue，同域或足迹重叠者靠依赖序或补位串行——冲突事前避而非事后解；`stage_hint` 已起的 issue（恢复场景）按 [references/recovery.md](references/recovery.md) 处置
2. 分流：`ready-for-agent` 进批次；`ready-for-human` 跳过——它与下游被阻塞链都不启动；无标签的读正文判断归类（batch-poll 的 `ready_to_start` 只算依赖与未起，triage 由你定）
3. 向用户展示批次计划：成员清单、依赖顺序、跳过项及连带不启动的下游、并发上限（默认 3，用户可覆盖）
4. **主动请求一次性前置授权**：向用户明确提出两项预批——本批所有 PR 的合并（含清尾轮立项的 PR）；清尾立项权限（对满足收尾节判据的缺陷类 follow-up，lead 可自行 /to-tickets 立项并在清尾轮跑到合并，被拒则清尾降级为收尾转呈）。连同流程将自动执行的动作边界（修改 triage 标签、PR 转 draft、在 Spec 发 QA 验收 comment；清尾授权之外不创建新 issue，gap 立项仍须用户中途指令）。这是本流程唯一的同步确认点；前置授权在此落入 lead 的 transcript，后续不再逐笔请示
5. 用户确认后建账本（首条 append，记录计划裁决与所得授权，见「账本」），进入无人值守执行，不再中途请示

## 第三步：组建团队，按依赖调度

TeamCreate 建团队。并发上限指同时进行的 issue 数（处于任一阶段都算）：并发越高，每次合并引发的重审与并发请示越多。

issue 的启动条件：全部 blocker 已合入 main。worktree 一律从最新 main 创建，不做跨分支依赖；blocker 被搁置时下游不启动，归入收尾清单。

每个 issue 由三个 teammate 接力，每个阶段使用干净上下文：

| 阶段 | 契约文件 | 交付物 |
|---|---|---|
| 实现 | [references/implementer.md](references/implementer.md) | 质量门通过的 worktree（基于最新 main，分支 issue/N，未建 PR） |
| 本地审查+建 PR | [references/local-reviewer.md](references/local-reviewer.md) | PR 号 |
| AI 审查循环 | [references/review-looper.md](references/review-looper.md) | 达标报告（可合并） |

spawn 时按 [references/spawn-prompts.md](references/spawn-prompts.md) 的模板填变量。三个阶段不要合并、不要让同一 teammate 连任：本地审查必须由未参与实现的上下文执行（实现者自查存在盲区），审查循环是长周期轮询、不应背负实现阶段的上下文。

每个 issue 配一份交接文件 `.afk/<batch-id>/handoff-<N>.md`：各阶段退役前按 [references/handoff.md](references/handoff.md) 追加本阶段的段，后续阶段开局读取；账本仍只由 lead 写入。

## 合并纪律

- 一次只合一笔。合并前核对 review-looper 的达标报告，核对以远端为准：`gh pr view <M> --json mergeable,headRefOid` 一次取回，确认 `mergeable` 为 MERGEABLE（只检查无冲突即可：本仓库合并不要求分支 up-to-date，分支落后 main 不阻塞合并），且达标报告所述达标 HEAD 与 `headRefOid` 一致——不采信 teammate 自报的 commit/push 事实
- squash 合并，标题沿用 PR 标题（squash 下它就是 changelog 条目）
- 合并后不广播——rebase 与冲突处置是 teammate 契约内置行为；健康检查的 `conflicting[]` 作兜底，发现 CONFLICTING 且对应 teammate 长时间无动作才定向提醒

## 裁决分类法

teammate 的一切暂停请示先到你这里（/pr-ai-review-loop 中"暂停询问用户"的场景在本流程一律重定向为请示 lead）。分三类处置：

1. **故障类**（bot 报错、quota 耗尽、长时间无响应）：自行裁决，不升级用户。按 /pr-ai-review-loop 故障节的建议重试一次；仍失败则本 PR 停用该 reviewer 并记录，收尾前可做一次补审尝试。即时 append 账本 `fault`（崩溃恢复需据此 replay），并纳入收尾汇报
2. **已答复又被重复提出的意见**：同一主题已有 pushback 在案、又被同一 reviewer 重复提出——不算真冲突、不搁置：裁决维持 pushback，令 looper 回评引用在案结论后继续循环；浮现出值得升级 ADR 的原则则记入收尾转呈，不当场写 ADR
3. **reviewer 真实冲突 / 业务取舍**：不选边，按 needs-human 搁置：PR 转 draft（draft 下 CodeRabbit 不审，冻结循环消除重审噪音）、issue 改 `ready-for-human`、PR 评论写明争点与双方立场、teammate 退役并清理 worktree（分支与 PR 留在远端待人工接手）、append 账本 `shelve`（含争点）并归入收尾清单

## 健康检查与替补

批次执行期间保持 ScheduleWakeup 定时唤醒（约 30 分钟一次）。每次唤醒跑一遍 batch-poll 取全批次远端快照（各 issue `stage_hint`、PR `updatedAt` / `mergeable`、`conflicting` / `merge_candidate`），结合 teammate 的 task 状态与最近一次汇报判断进展——batch-poll 不判定 teammate 存活状态。长时间无进展且无合理等待理由（等待 reviewer 响应属合理）→ SendMessage 询问；无回应则判定该 teammate 已失效，按 spawn-prompts.md 的替补附言 spawn 替补接管。

teammate 的 idle 通知不是事件：常规 idle（无伴随请示或交付消息）一律不动作、不输出、不为此提前跑 batch-poll——looper 的 idle 是轮询循环的常态，实现与本地审查阶段的失效也只在定时健康检查中判定（idle 通知与交付消息可能乱序，即时反应易误判）。

## 账本

`.afk/<batch-id>.jsonl` 是一份追加式薄账本，只记 **gh/git 无法重推的事实**；远端可查的（issue / PR / 分支状态、依赖图）一律不落账、不镜像，需要时跑 batch-poll。它是恢复 replay 的依据，也是收尾复盘与审计的来源。

用 `ledger.sh` 追加，不要用裸 `echo >>`：

```bash
bash .agents/skills/afk-team-workflow/scripts/ledger.sh <batch-id> <kind> [--issue N] [--pr M] [--scope-spec N | --scope-issues "1,2,3"] [--detail "..."]
```

- **batch-id**：Spec 批次用 `spec-<N>`；显式 issue 批次用一个 slug（如 `batch-<日期>`）
- **scope（首条必填）**：首条记录批次成员，Spec 批次用 `--scope-spec <N>`，slug 批次用 `--scope-issues "1,2,3"`（slug 的 batch-id 不含成员信息，恢复靠 scope 行重建）
- **全程 append，按 kind 落账**：`decision`（计划与清尾分拣裁决）、`authorization`（用户口头授权；仅作恢复 replay 的信息参考，不作执行凭证）、`fault`（吸收的故障 / 停用的 reviewer）、`gap`（已浮现的 Spec 缺口）、`shelve`（搁置为 needs-human 的 issue 及争点）、`merge`（已执行的合并）、`retrospective`（review-looper 交来的 per-PR 复盘）、`closed`（收尾终态行）
- **生命周期**：第二步用户确认时写首条（create）→ 全程 append → 收尾写 `closed`，**不删除**。`.afk/` 已 gitignored，账本是本地运维状态，永不提交

## 发现 Spec 落点缺口时

gap 专指功能性缺口：Spec 有要求但任何子 issue 均未覆盖——"未覆盖"可能是用户拆解时的有意裁剪，故必须人工确认，不入清尾授权；批内发现的缺陷类 follow-up 不走本节，按收尾的清尾轮处置。发现 gap 时：SendUserMessage（proactive）实时提醒用户，说明缺口描述、建议与对本批次的影响，不阻塞批次继续。用户中途授权则用 /to-tickets 立项并按依赖加入批次；未获回复则相关 issue 按字面验收标准收口。append 账本 `gap`，并记入收尾转呈与 QA comment。

## 收尾

全部计划成员到达终态（已合并或已搁置）后，先清尾、再收口：

1. **清尾轮（单轮）**：聚合账本与 handoff 目录中的 follow-up 候选，逐条分拣——同时满足①真实缺陷且有实证（非猜测）②本批合并引入/遗留、或阻碍 Spec 验收路径③修复方向唯一明确、无业务取舍，才算批内应收；涉及方案选择、性能权衡、改进增强或拿不准的一律转呈。持清尾授权时，应收项用 /to-tickets 立项并跑到终态：其"与用户确认拆分"环节以清尾授权代之，issue 挂 Spec sub-issue、保持 `## Blocked by` 模板格式（batch-poll 的解析依赖），接力与合并纪律同第三步。分拣结果 append 账本 `decision`；`--issues` 批次扩员后补一条带 scope 的 `decision` 行。清尾轮中新滋生的候选一律转呈，不滋生下一轮；未获清尾授权则本步跳过、候选全部转呈
2. **在 Spec issue 发人工 QA 验收清单 comment，不关闭 Spec 本体**。清单按已合并子 issue（含清尾轮）组织：每项给 PR 链接与面向用户可感知行为的验收步骤（实际操作路径，不复读技术验收标准）；末尾列 needs-human 搁置项、跳过与未启动项、发现的缺口。纯 issue 列表批次没有共同 Spec 时，清单并入收尾汇报
3. 解散团队，删除全部 worktree 与本地分支（远端分支合并后自动删除）
4. 向用户汇报三份清单：已合并（issue 与 PR 对照）、needs-human 搁置（含争点）、跳过与未启动（含原因）；另附转呈事项：缺口立项建议、故障裁决记录、清尾分拣中转呈的候选，以及**聚合复盘**——从账本 `retrospective` 行与 handoff 目录聚合四类复盘候选（ADR / CONTEXT.md / CLAUDE.md / follow-up），一次性呈用户裁决。多数批次干净收敛，四类候选常为空；空是预期结果，照实呈报，无需为"没有候选"补叙
5. 账本 append 一条 `closed` 收尾行（`bash .agents/skills/afk-team-workflow/scripts/ledger.sh <batch-id> closed`）——账本不删除，留作复盘源与审计，并供下次触发时的恢复探测器据此判定本批次已终态。批准后的复盘落地方式（写 ADR / 改 CONTEXT.md / 补 CLAUDE.md / 立 follow-up issue）不在此指定，由用户与后续会话决定
