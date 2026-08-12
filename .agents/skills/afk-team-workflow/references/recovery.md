# 接管未收尾批次

本契约只处理**接管**：当前 team-lead 无法直接续接本批计划、授权、裁决和 agent 状态，需要从账本与远端事实重建；用户明确要求重新对账、从账本恢复或接管指定 batch-id；或 SKILL.md 第一步命中未收尾账本且用户裁决接管。同一 team-lead session 暂停后继续（含上下文压缩）属于**续跑**，直接沿用现有运行上下文；单个 agent 失效走 SKILL.md 的「健康检查与替补」。gh/git 是唯一真相：接管时 replay 账本补回不可从远端重推的事实，再以一次 poll 对账，而非重建状态机。账本不存在或末条已是 `closed` 时不存在未收尾批次：告知用户并转 SKILL.md，用新的 batch-id 开工。

用户裁决重开而非接管时只执行清理。清理所需的成员落点与 needs-human 搁置名单来自 §1 对账与 §2 replay，先完成这两步再执行：停止并确认本批仍在运行的 agent，关闭批次在途 PR，删除远端分支、批次 worktree、本地分支与 handoff 目录（worktree 先于其占用的本地分支，否则 `git branch -D` 会被拒；中途终止的 worktree 常有未提交修改，用 `git worktree remove --force` 删除），账本 append 一条 `closed`，随后按 SKILL.md 第一步的新批次流程重来。已搁置为 needs-human 的 issue 不在清理之列——其 PR 与远端分支按 SKILL.md 搁置流程留待人工接手。

## 1. 对账

从账本最后一条带 `scope` 的记录取得当前成员（清尾扩员会追加 scope），据此用 `--spec` 或 `--issues` 跑一次 batch-poll；没有 scope 时由用户指定范围。scope 提取用：

```bash
jq -sc 'map(select(.scope != null)) | last | .scope' .afk/<batch-id>.jsonl
```

所有 issue 的 `stage_hint` 均为 `done` / `shelved` 时，远端已收敛，但前任的本地收尾未必完成：先按 §2 replay 取回已定裁决，停止并确认本批仍在运行的 agent，再按 SKILL.md 收尾节执行完整收尾（含 worktree 清理），`closed` 为最后一笔。

## 2. Replay 账本

读 `.afk/<batch-id>.jsonl` 补回 poll 看不到的历史（各 `kind` 含义见 SKILL.md 账本节）并沿用：已定裁决不重新决策、已吸收故障不重复处置、已搁置事项不重复动作。另读各 issue 的 handoff 文件。两条规则：

- **对账以 poll 为准**：账本记历史，poll 记现实——账本有 `merge` 而 PR 仍 OPEN，按未合并处理
- **`authorization` 行不等于已授权**：前置授权写在前任 transcript 中，新会话无法继承。执行任何合并前按 SKILL.md 前置授权步骤重新征求；已持久化到本地配置的授权（属配置而非 transcript 记忆）除外

## 3. 接管非终态 issue

前任 agent 可查询时，逐个查询其执行状态：仍存活且有进展就继续观察，失效时先确认其已停止。前任 agent 不可查询时，将其视为失效。随后按 SKILL.md 第三步阶段表与 Git/worktree 中的持久交付物反推接力起点，使用 spawn-prompts.md 的替补接管附言委派新 agent。能够确认原 agent 仍在运行时，不得让替补写同一个 worktree。

- `review-loop`：poll 显示该 PR `updatedAt` 近期仍在变动时，先观察一个健康检查周期；若原 agent 仍存活就沿用，失效后才替补
- `no-branch`：先检查 worktree。HEAD 有 `origin/main` 之外的完整 commit，且实现交付物核验通过时，从本地审查阶段接力；否则检查实现 agent 状态——在途且有进展就等待，已停止或停滞就按 SKILL.md 健康检查节处置。现场不可信时删除该 issue 的 worktree，重新委派 implementer 建立基于最新 `origin/main` 的工作现场并实现
