# 接管未收尾批次

用户显式要求接管或恢复某批次，或 SKILL.md 第一步的同批次检查命中且用户裁决接管时，加载本契约。gh/git 是唯一真相：恢复即 replay 账本补回不可从远端重推的事实，再以一次 poll 对账，而非重建状态机。账本文件可能含多段生命周期——`closed` 后同一 batch-id 再开批会续写同一文件——对账与 replay 一律只取最后一条 `closed` 之后的段。账本不存在或末条已是 `closed` 时不存在未收尾批次：告知用户并转 SKILL.md 新批次流程。

用户裁决重开而非接管时只执行清理。清理所需的成员落点、needs-human 搁置名单与各 issue 实现路线来自 §1 对账与 §2 replay，先完成这两步再执行：cancel 在途 codex 任务，关闭批次在途 PR，删除远端分支、批次 worktree、本地分支与 handoff 目录（worktree 先于其占用的本地分支，否则 `git branch -D` 会被拒），账本 append 一条 `closed`，随后按 SKILL.md 第一步的新批次流程重来。已搁置为 needs-human 的 issue 不在清理之列——其 PR 与远端分支按 SKILL.md 搁置流程留待人工接手。

## 1. 对账

对该 batch-id 跑一次 batch-poll：`spec-<N>` 批次直接 `--spec <N>`；slug 批次的成员取当前生命周期段内**最后一条带 `scope` 的行**（清尾扩员会追加 scope 行），据此 `--issues`；当前段内无 scope 行时由用户指定范围。

所有 issue 的 `stage_hint` 均为 `done` / `shelved` 时，远端已收敛，但前任的本地收尾未必完成：先按 §2 replay 当前段取回实现路线与已定裁决，据此对路线为 codex 的 issue 在其 worktree 跑 companion `cancel` 停掉仍存活的任务，再按 SKILL.md 收尾节执行完整收尾（含 worktree 清理），`closed` 为最后一笔。

## 2. Replay 账本

读 `.afk/<batch-id>.jsonl` 补回 poll 看不到的历史（各 `kind` 含义见 SKILL.md 账本节）并沿用：已定裁决不重新决策、已吸收故障不重复处置、已搁置事项不重复动作。另读各 issue 的 handoff 文件。两条规则：

- **对账以 poll 为准**：账本记历史，poll 记现实——账本有 `merge` 而 PR 仍 OPEN，按未合并处理
- **`authorization` 行不等于已授权**：前置授权写在前任 transcript 中，新会话无法继承。执行任何合并前按 SKILL.md 前置授权步骤重新征求；已持久化到本地配置的授权（属配置而非 transcript 记忆）除外

## 3. 对非终态 issue 重 spawn

前任 team-lead 终止后其 teammate 不可达也不可问责，一律重 spawn 替补——假死 teammate 与新上下文并发驱动同一 PR 比重复劳动更糟。接力起点按 SKILL.md 第三步阶段表的交付物反推，使用 spawn-prompts.md 的替补接管附言。两个特例：

- `review-loop`：poll 显示该 PR `updatedAt` 近期仍在变动时，先观察一个唤醒周期再重 spawn，避免两个上下文同时推同一 PR
- `no-branch` 且路线为 codex：codex 后台任务不随 team-lead 会话终止而死。先看 worktree：HEAD 有 `origin/main` 之外的 commit（前任已代 commit）→ 实现已交付，从本地审查阶段接力；否则在对应 worktree 跑 companion（codex 插件的 `codex-companion.mjs`）`status --all` 分三态处置：已完成 → 按 SKILL.md「实现路线与模型」以 `result` 交付并核验，不重跑；在途且有进展 → 等待其完成；失败或停滞 → 按 SKILL.md 健康检查节的替补处置执行
