---
name: afk-team-workflow
description: 把一个 PRD 的全部子 issue（或一组显式 issue）组建团队无人值守跑到全部合并：依赖调度、实现→本地审查→AI 审查循环三段接力、lead 串行合并与裁决、健康检查、收尾发 QA 验收清单。用户要"把某个 PRD 跑完 / AFK 消化这批 issue / 组团队批量执行 / 把这几个 issue 跑掉"时使用——即使只给一个 PRD 编号说"干完它"、未提团队或 AFK，也应触发。
---

# AFK 团队执行流程

你是 lead：组建团队，把一批 issue 无人值守推进到全部合并或明确搁置。你负责调度、合并、裁决与健康检查，自己不写代码；实现、本地审查、外部审查循环、补立项分别交给 /tdd、/code-review、pr-ai-review-loop、/to-issues。

## 第一步：确定批次成员

输入有两种形态：

- **PRD 编号**：用 `gh api repos/{owner}/{repo}/issues/<N>/sub_issues` 列出全部子 issue，与标题尾缀 `[PRD #N]` 交叉核对
- **显式 issue 列表**：直接作为批次成员，适用于跨 PRD 的批次

逐个通读 issue 正文与评论。issue 操作惯例见 `docs/agents/issue-tracker.md`，triage 标签语义见 `docs/agents/triage-labels.md`。

## 第二步：制定计划，请用户确认一次

1. 按正文 Blocked by 建依赖图
2. 分流：`ready-for-agent` 进批次；`ready-for-human` 跳过——它与下游被阻塞链都不启动；无标签的读正文判断归类
3. 向用户展示批次计划：成员清单、依赖顺序、跳过项及连带不启动的下游、并发上限（默认 3，用户可覆盖）
4. 同时声明授权边界：流程将自动合并 PR、修改 triage 标签、PR 转 draft、在 PRD 发 QA 验收 comment；不会自行创建新 issue——立项永远源于用户指令
5. 用户确认后进入无人值守执行，不再中途请示

## 第三步：组建团队，按依赖调度

TeamCreate 建团队。并发上限指同时进行的 issue 数（处于任一阶段都算），默认 3：进行中的 PR 越多，每次合并引发的 rebase 与重审越多，这个数字同时把并发的裁决请求压在可从容处理的范围内。

issue 的启动条件：全部 blocker 已合入 main。worktree 一律从最新 main 创建，不做跨分支依赖；blocker 被搁置时下游不启动，归入收尾清单。

每个 issue 由三个 teammate 接力，每个阶段使用干净上下文：

| 阶段 | 契约文件 | 交付物 |
|---|---|---|
| 实现 | [references/implementer.md](references/implementer.md) | 质量门通过的 worktree（基于最新 main，分支 issue/N，未建 PR） |
| 本地审查+建 PR | [references/local-reviewer.md](references/local-reviewer.md) | PR 号 |
| AI 审查循环 | [references/review-looper.md](references/review-looper.md) | 达标报告（可合并） |

spawn 时按 [references/spawn-prompts.md](references/spawn-prompts.md) 的模板填变量。三个阶段不要合并、不要让同一 teammate 连任：本地审查必须由未参与实现的上下文执行（实现者自查存在盲区），审查循环是长周期轮询、不应背负实现阶段的上下文。

## 合并纪律

- 一次只合一笔。合并前核对 review-looper 的达标报告，并确认 `gh pr view <M> --json mergeable` 为 MERGEABLE——只检查无冲突即可：本仓库合并不要求分支 up-to-date，分支落后 main 不阻塞合并
- squash 合并，标题沿用 PR 标题（squash 下它就是 changelog 条目）
- 每合并一笔，向所有进行中的 teammate 广播"main 已前进"。teammate 不必立即 rebase，随下次修复 push 一并完成——每次 push 都会触发全部 reviewer 重审一轮；PR 进入 CONFLICTING 才要求立即解冲突

## 裁决分类法

teammate 的一切暂停请示先到你这里（pr-ai-review-loop 中"暂停询问用户"的场景在本流程一律重定向为请示 lead）。分三类处置：

1. **故障类**（bot 报错、quota 耗尽、长时间无响应）：自行裁决，不升级用户。按 pr-ai-review-loop 故障节的建议重试一次；仍失败则本 PR 停用该 reviewer 并记录，收尾前可做一次补审尝试。裁决记录进收尾汇报
2. **已答复又被重复提出的意见**：同一主题已有 pushback 在案、又被同一 reviewer 重复提出——不算真冲突、不搁置，交 review-looper 按 pr-ai-review-loop 的收敛兜底处理；其暂停按重定向请示你逐案定。浮现出值得升级 ADR 的原则记入收尾转呈，不当场写 ADR
3. **reviewer 真实冲突 / 业务取舍**：不选边，按 needs-human 搁置：PR 转 draft（draft 下 CodeRabbit 不审，冻结循环消除重审噪音）、issue 改 `ready-for-human`、PR 评论写明争点与双方立场、teammate 退役并清理 worktree（分支与 PR 留在远端待人接手）、归入收尾清单

## 健康检查与替补

批次执行期间保持 ScheduleWakeup 定时唤醒（约 30 分钟一次）。每次唤醒核对每个进行中 issue 的可观察信号：task 状态、PR updatedAt、分支 HEAD、最近一次汇报。长时间无进展且无合理等待理由（等待 reviewer 响应属于合理）→ SendMessage 询问；无回应则判定该 teammate 已失效，按 spawn-prompts.md 的替补附言 spawn 替补接管。

## 发现 PRD 落点缺口时

发现 PRD 有要求但任何子 issue 都未覆盖的缺口：SendUserMessage（proactive）实时提醒用户——缺口描述、建议、对本批次的影响——不阻塞批次继续。用户中途授权则用 /to-issues 立项并按依赖加入批次；未获回复则相关 issue 按字面验收标准收口，缺口记入收尾转呈与 QA comment。

## 收尾

全部可执行 issue 到达终态（已合并或已搁置）后：

1. **在 PRD issue 发人工 QA 验收清单 comment，不关闭 PRD 本体**。清单按已合并子 issue 组织：每项给 PR 链接与面向用户可感知行为的验收步骤（实际操作路径，不复读技术验收标准）；末尾列 needs-human 搁置项、跳过与未启动项、发现的缺口。纯 issue 列表批次没有共同 PRD 时，清单并入收尾汇报
2. 解散团队，删除全部 worktree 与本地分支（远端分支合并后自动删除）
3. 向用户汇报三份清单：已合并（issue 与 PR 对照）、needs-human 搁置（含争点）、跳过与未启动（含原因）；另附转呈事项：ADR 候选、缺口立项建议、故障裁决记录
