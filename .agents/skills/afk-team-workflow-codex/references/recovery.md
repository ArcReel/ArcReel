# 崩溃恢复

入口发现 `.afk/*.jsonl` 末条不是 `closed` 时加载本页。Claude 与 Codex 共享 `.afk/`、batch-id 和 handoff，以 ledger、handoff 与 gh/git 现场完成接管。

恢复的核心是 replay 无法从 gh/git 重推的事实，再用新 poll 对账。开始前仍须执行 [preflight.md](preflight.md)；任何新权限失败都停止在用户在线阶段。

## 1. 防御式盘点

在改变现场前读取：

- 账本全部行及最后一条带 `scope` 的行；
- `.afk/<batch-id>/handoff-*.md`；
- `git worktree list --porcelain`；
- 本地 `issue/*` 分支、远端分支与 worktree 的 `git status`；
- fresh `batch-poll.sh` 输出、现有 PR 和 issue 评论。

`spec-<N>` 用 `--spec N`；slug 批次用最后一条带 scope 的 `issues`。无 scope 时无法自动确定成员，必须让用户给范围。任何 worktree 脏改、分支 diverge、PR 活跃更新都作为现场保留，不能覆盖或清理。

若所有成员已是 `done`/`shelved`，仍完整执行清尾：可能只差 QA、worktree/heartbeat 清理与 `closed`，不能只补一行。

## 2. 让用户选择

向用户列出 batch-id、终态/在途分布、worktree/分支/PR 现场，以及账本中的授权、搁置争点和故障。只提供：

- **接管**：replay 后续跑；
- **重开**：保留现场，回主 skill 重新规划，不覆盖旧账；
- **忽略**：不改账本、不建 heartbeat、不 spawn。

等待用户明确选择。若选择重开，新 batch-id 不得与未关闭账本冲突。

## 3. Replay 不可重推事实

按 `kind` 恢复：

| kind | 恢复内容 |
|---|---|
| `decision` | 并发、范围与清尾分拣取舍 |
| `authorization` | 曾经的口头授权，仅供说明，不能跨会话执行 |
| `fault` | 已停用 reviewer 与已吸收故障 |
| `gap` | 已向用户浮现的功能缺口 |
| `shelve` | needs-human 争点 |
| `merge` | 历史合并意图，以 fresh poll 复核 |
| `retrospective` | 待聚合 per-PR 复盘 |

账本与远端冲突时信 fresh poll。例如账本有 `merge` 而 PR 仍 open，按未合并处理。

## 4. 接管在途阶段

前任 lead 的 teammate 不可达；不要尝试重连。按远端和本地现场选择阶段，并用 [spawn-prompts.md](spawn-prompts.md) 的替补附言新建 `fork_turns:"none"` agent：

| 现场 | 接管阶段 |
|---|---|
| 无 PR，worktree 缺少完整“实现”handoff、存在未提交实现改动或质量门未知 | 实现 |
| 无 PR，worktree 干净、实现 commit 与完整“实现”handoff 都存在；或已有远端分支 | 独立本地审查 |
| open 非 draft PR | AI 审查循环 |
| draft/closed 未合并 PR | 已搁置，除非用户明确重开 |

`review-loop` PR 的 `updatedAt` 近期仍变化时先观察一个 lead heartbeat 周期，避免两个上下文同时推同一 PR。替补 prompt 必须携带绝对 worktree 和 handoff；若只有远端分支没有 worktree，lead 在确认未被别处占用后为该分支恢复 worktree。

## 5. 重新授权与新 heartbeat

接管会话在执行任何合并或清尾立项前，重新展示剩余计划并请求主 skill 的两项前置授权。旧 `authorization` 只说明“曾授权”。重新授权后追加新的 `authorization` 行，再为当前 task 创建新的 lead heartbeat；旧 task heartbeat 若可见则删除。

完成条件：fresh poll 与账本已对账，用户重新授权，且每个非终态 issue 只有一个新阶段 agent；最终仍按 lead 契约清尾并追加 `closed`。
