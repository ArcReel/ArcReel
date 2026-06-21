---
name: afk-team-workflow
description: 把一个 PRD 的全部子 issue（或一组显式 issue）组建团队无人值守跑到全部合并：依赖调度、实现→本地审查→AI 审查循环三段接力、lead 串行合并与裁决、健康检查、收尾发 QA 验收清单。用户要"把某个 PRD 跑完 / AFK 消化这批 issue / 组团队批量执行 / 把这几个 issue 跑掉"时使用——即使只给一个 PRD 编号说"干完它"、未提团队或 AFK，也应触发。
---

# AFK 团队执行流程

你是 lead：组建团队，把一批 issue 无人值守推进到全部合并或明确搁置。你负责调度、合并、裁决与健康检查，自己不写代码；实现、本地审查、外部审查循环、补立项分别交给 /tdd、/code-review、/pr-ai-review-loop、/to-issues。

## 触发前先检查未完成批次

开工前检查 `.afk/` 是否存在**末条事件不是 `closed`** 的账本（`closed` 只在末条才表示收尾，中段出现不算）。逐文件用 jq 解析末条的 `.kind` 字段（而非 `grep` 子串匹配——崩溃截断或损坏的尾行子串判定不可靠，jq 解析失败时按未收尾处理、触发恢复，取安全侧；且与 ledger.sh 的 jq 写入一致），例如 `for f in .afk/*.jsonl; do [ -f "$f" ] || continue; tail -n1 "$f" | jq -e '.kind == "closed"' >/dev/null 2>&1 || echo "$f"; done` 列出未收尾账本（`.afk/` 无账本时通配符不展开，`[ -f ]` 守卫跳过字面量）。若有，则上一会话的 lead 可能中途终止——读 [references/recovery.md](references/recovery.md) 按接管流程处理，不要当作全新批次直接覆盖。若无，照常继续。

## 第一步：确定批次成员

先跑 batch-poll 取批次的机械底图：展开 PRD 子 issue、解析依赖图、给出每个 issue 的远端落点（标签、`blocked_by`、分支/PR 状态、`stage_hint`），规划、健康检查、恢复三处共用：

```bash
bash .agents/skills/afk-team-workflow/scripts/batch-poll.sh --prd <N>       # PRD 编号：展开其 GitHub 子 issue
bash .agents/skills/afk-team-workflow/scripts/batch-poll.sh --issues 1,2,3  # 跨 PRD 的显式 issue 集
```

batch-poll 只产出 gh/git 事实与机械汇总，不做语义判断。取得底图后**逐个通读 issue 正文与评论**补足语义：验收边界、隐含取舍，以及 batch-poll 的 `blocked_by` 是否被非常规正文误导（它按 `## Blocked by` 约定机械解析，散文写法以通读结论为准）。issue 操作惯例见 `docs/agents/issue-tracker.md`，triage 标签语义见 `docs/agents/triage-labels.md`。

## 第二步：制定计划，主动请求一次前置授权

1. 依赖顺序按 batch-poll 的 `blocked_by` / `ready_to_start` 排；`stage_hint` 已起的 issue（恢复场景）按 [references/recovery.md](references/recovery.md) 处置
2. 分流：`ready-for-agent` 进批次；`ready-for-human` 跳过——它与下游被阻塞链都不启动；无标签的读正文判断归类（batch-poll 的 `ready_to_start` 只算依赖与未起，triage 由你定）
3. 向用户展示批次计划：成员清单、依赖顺序、跳过项及连带不启动的下游、并发上限（默认 3，用户可覆盖）
4. **主动请求一次性前置授权**：向用户明确提出"本批所有 PR 的合并是否预先批准"，连同流程将自动执行的动作边界（修改 triage 标签、PR 转 draft、在 PRD 发 QA 验收 comment；不自行创建新 issue，立项永远源于用户指令）。这是本流程唯一的同步确认点；前置授权在此落入 lead 的 transcript，后续合并不再逐笔请示。合并是 lead 的低频高危动作，走口头预批（遵 memory `feedback_verbal_approval_no_settings_edit`）。若本批历经上下文压缩，先确认这条口头授权仍在 transcript；丢失则该笔合并重新征求，不默认仍预批
5. 用户确认后建账本（首条 append，记录计划裁决与所得授权，见「账本」），进入无人值守执行，不再中途请示

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

teammate 的一切暂停请示先到你这里（/pr-ai-review-loop 中"暂停询问用户"的场景在本流程一律重定向为请示 lead）。分三类处置：

1. **故障类**（bot 报错、quota 耗尽、长时间无响应）：自行裁决，不升级用户。按 /pr-ai-review-loop 故障节的建议重试一次；仍失败则本 PR 停用该 reviewer 并记录，收尾前可做一次补审尝试。即时 append 账本 `fault`（崩溃恢复需据此 replay），并纳入收尾汇报
2. **已答复又被重复提出的意见**：同一主题已有 pushback 在案、又被同一 reviewer 重复提出——不算真冲突、不搁置，交 review-looper 按 /pr-ai-review-loop 的收敛兜底处理；其暂停按重定向请示逐案裁定。浮现出值得升级 ADR 的原则则记入收尾转呈，不当场写 ADR
3. **reviewer 真实冲突 / 业务取舍**：不选边，按 needs-human 搁置：PR 转 draft（draft 下 CodeRabbit 不审，冻结循环消除重审噪音）、issue 改 `ready-for-human`、PR 评论写明争点与双方立场、teammate 退役并清理 worktree（分支与 PR 留在远端待人工接手）、append 账本 `shelve`（含争点）并归入收尾清单

## 健康检查与替补

批次执行期间保持 ScheduleWakeup 定时唤醒（约 30 分钟一次）。每次唤醒跑一遍 batch-poll 取全批次远端快照（各 issue `stage_hint`、PR `updatedAt` / `mergeable`、`conflicting` / `merge_candidate`），结合 teammate 的 task 状态与最近一次汇报判断进展。长时间无进展且无合理等待理由（等待 reviewer 响应属合理）→ SendMessage 询问；无回应则判定该 teammate 已失效，按 spawn-prompts.md 的替补附言 spawn 替补接管。batch-poll 只报告"远端无分支 / PR 无变动"，是否失效仍需结合 task 状态裁断——它不判定 teammate 存活状态。

## 账本

`.afk/<batch-id>.jsonl` 是一份追加式薄账本，只记 **gh/git 无法重推的事实**；远端可查的（issue / PR / 分支状态、依赖图）一律不落账、不镜像，需要时跑 batch-poll。它是恢复 replay 的依据，也是收尾复盘与审计的来源。

用 `ledger.sh` 追加（确定性 append + 时间戳 + 合法 JSONL；不要用裸 `echo >>`，以免 detail 中的引号或换行写坏账本）：

```bash
bash .agents/skills/afk-team-workflow/scripts/ledger.sh <batch-id> <kind> [--issue N] [--pr M] [--scope-prd N | --scope-issues "1,2,3"] [--detail "..."]
```

- **batch-id**：PRD 批次用 `prd-<N>`；显式 issue 批次用一个 slug（lead 是普通会话，可用 `date` 生成 `batch-<日期>`）
- **scope（首条必填）**：首条记录批次成员，PRD 批次用 `--scope-prd <N>`，slug 批次用 `--scope-issues "1,2,3"`。slug 的 batch-id 不含成员信息，恢复时据首条 `scope` 重建 `--issues` 参数，不必解析自由文本 detail
- **全程 append，按 kind 落账**：`decision`（计划裁决）、`authorization`（用户口头授权）、`fault`（吸收的故障 / 停用的 reviewer）、`gap`（已浮现的 PRD 缺口）、`shelve`（搁置为 needs-human 的 issue 及争点）、`merge`（已执行的合并）、`retrospective`（review-looper 交来的 per-PR 复盘）、`closed`（收尾终态行）
- **生命周期**：第二步用户确认时写首条（create）→ 全程 append → 收尾写 `closed`，**不删除**。`.afk/` 已 gitignored，账本是本地运维状态，永不提交

## 发现 PRD 落点缺口时

发现 PRD 有要求但任何子 issue 均未覆盖的缺口时：SendUserMessage（proactive）实时提醒用户，说明缺口描述、建议与对本批次的影响，不阻塞批次继续。用户中途授权则用 /to-issues 立项并按依赖加入批次；未获回复则相关 issue 按字面验收标准收口。append 账本 `gap`，并记入收尾转呈与 QA comment。

## 收尾

全部可执行 issue 到达终态（已合并或已搁置）后：

1. **在 PRD issue 发人工 QA 验收清单 comment，不关闭 PRD 本体**。清单按已合并子 issue 组织：每项给 PR 链接与面向用户可感知行为的验收步骤（实际操作路径，不复读技术验收标准）；末尾列 needs-human 搁置项、跳过与未启动项、发现的缺口。纯 issue 列表批次没有共同 PRD 时，清单并入收尾汇报
2. 解散团队，删除全部 worktree 与本地分支（远端分支合并后自动删除）
3. 向用户汇报三份清单：已合并（issue 与 PR 对照）、needs-human 搁置（含争点）、跳过与未启动（含原因）；另附转呈事项：ADR 候选、缺口立项建议、故障裁决记录，以及**聚合复盘**——review-looper 随达标报告交来的 per-PR 复盘候选已逐份 append 进账本（`retrospective`），收尾时从账本聚合、并入转呈事项一次性呈用户裁决。多数批次干净收敛，三类复盘候选（ADR / CONTEXT.md / follow-up）常为空；空是预期结果，照实呈报，无需为"没有候选"补叙
4. 账本 append 一条 `closed` 收尾行（`bash .agents/skills/afk-team-workflow/scripts/ledger.sh <batch-id> closed`）——账本不删除，留作复盘源与审计，并供下次触发时的恢复探测器据此判定本批次已终态。批准后的复盘落地方式（写 ADR / 改 CONTEXT.md / 立 follow-up issue）不在此指定，由用户与后续会话决定
