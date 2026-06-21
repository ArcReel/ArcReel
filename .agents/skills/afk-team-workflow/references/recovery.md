# 崩溃恢复：接管未完成批次

lead 崩溃是小概率事件，本契约是兜底而非常设机制。仅当 SKILL.md 入口扫描发现 `.afk/` 下存在**缺少 `closed` 收尾行**的账本时加载——这意味着上一会话的 lead 可能中途终止。

判据沿用 skill 主线：**gh/git 是唯一真相**。账本只记不可从远端重推的事实（裁决、口头授权、故障、缺口、搁置、合并、复盘），其余一律由 `batch-poll.sh` 现场重算。因此恢复的本质是 replay 账本补回崩溃丢失的上下文，再以一次新 poll 对账，而非重建状态机。

## 1. 确认是否需要恢复

入口扫描已选出缺 `closed` 行的账本。对其 batch-id 跑一次 poll：`prd-<N>` 批次的 N 已在 batch-id 中，直接 `--prd <N>`；slug 批次的 batch-id 不含成员，成员取自账本首条的 `scope.issues`，据此 `--issues`（旧账本首条若无 `scope`，恢复无法自动确定成员，须人工指定范围）：

- 每个 issue 的 `stage_hint` 均为 `done` / `shelved` → 批次实际已收敛，仅前任 lead 未及写 `closed`。但 poll 只证明远端收敛，无法证明前任会话已完成本地收尾，故仍按 SKILL.md 收尾节执行完整收尾（含 worktree 与本地分支清理）、补 `closed` 行并汇报，不止于补 `closed`。
- 存在非终态 issue（`no-branch` / `local-review` / `review-loop`）→ 进入下方接管流程。

## 2. 询问用户：接管 / 重开 / 忽略

询问方式不限（proactive 消息或提问皆可，按场景定）。列出判断材料：批次标识、poll 显示的终态/在途分布、账本中无法从远端重推的事实（已给授权、已搁置争点、已记故障）。

- **接管**：续跑本批次，走下方 §3–§6。
- **重开**：弃用现状、从零重新规划，回 SKILL.md 第一步。
- **忽略**：保持原样，账本不动，不安排唤醒。

## 3. Replay 账本：仅取不可重推的事实

读 `.afk/<batch-id>.jsonl`，按 `kind` 补回 poll 看不到的历史：

| kind | 补回内容 |
|---|---|
| `decision` | 已定的规划取舍（并发上限、范围裁断），不重新决策 |
| `authorization` | 用户**曾**批准的事项——对再授权仅作**信息性**参考（见 §5），不等于已重新授权 |
| `fault` | 已停用的 reviewer / 已吸收的故障，不重复处置 |
| `gap` | 已向用户浮现的 PRD 缺口 |
| `shelve` | 已搁置为 needs-human 的 issue 及其争点（poll 显示 shelved，账本说明原因） |
| `merge` | 已执行的合并（以 poll 复核） |
| `retrospective` | 已收集、待并入收尾的 per-PR 复盘 |

**对账以 poll 为准**：账本记意图与历史，poll 记当下现实。两者冲突时信 poll——账本有 `merge` 而 poll 显示该 PR 仍 OPEN，说明合并未落地，按未合并处理。

## 4. 对非终态 issue 防御式重 spawn

用户已确认前任 lead 终止。lead 终止后其 teammate 即不可达、不可问责，**不要尝试重连旧 teammate**——按 `stage_hint` 对每个非终态 issue 重新 spawn：

| stage_hint | 重 spawn 的阶段 |
|---|---|
| `no-branch` | 实现者（implementer） |
| `local-review` | 本地审查者（local-reviewer） |
| `review-loop` | 审查循环负责人（review-looper） |

使用 [spawn-prompts.md](spawn-prompts.md) 的替补接管附言——新 teammate 须自行核查 worktree / 分支 / PR 现场，不假设任何未留痕的步骤已完成。

- **`review-loop` 特例**：先看 poll 给出的该 PR `updatedAt`。若近期仍在变动（teammate 或 reviewer bot 可能仍在操作），先观察一个唤醒周期再决定是否重 spawn，避免两个上下文同时向同一 PR 推送。
- **为何防御式而非乐观重连**：新会话无法观测 teammate 存活状态；一个假死但仍在运行的 teammate 与新上下文同时驱动同一 PR，比"重 spawn + 先核查现场"更糟。先核查的重 spawn 对"已终止"与"存活但不可达"两种情形都成立。

## 5. 重新征求授权

前置授权（如批量合并）写在**前任 lead 的 transcript** 中，新会话无法读取；账本的 `authorization` 行是"曾授权"的记录，**不是**跨会话的重新授权。执行任何合并前，按 SKILL.md 前置授权步骤**重新征求一次**。

例外：已持久化到本地配置的授权（如 teammate 高频动作的 allow）跨会话有效，属配置而非 transcript 记忆，无需重问；仅口头 / transcript 授权需重新征得。

## 6. 幂等

各步骤均可安全重跑：账本 append-only、poll 无状态、重 spawn 先核查现场。恢复流程本身若被打断，重新触发即可再执行一次。
