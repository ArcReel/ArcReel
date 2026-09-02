# Handoff

每个 issue 一份 `.afk/<batch-id>/handoff-<N>.md`；stage review loop 使用 `.afk/<batch-id>/handoff-stage-<K>.md`。各角色只追加自己的段，只写 diff、issue、PR 无法重推的判断；handoff 是交接便签，不是工作日志。

follow-up 只记候选，立 issue 归 team-lead 并受用户授权约束。

## Issue handoff

### 实现

- 关键取舍与理由
- 特殊运行环境
- 已知薄弱点
- follow-up 候选

### 本地审查

- 已修复 findings
- 跳过项与理由
- rebase / 冲突处置
- follow-up 候选

## Stage handoff

### Base sync

- 触发原因与冲突处置
- rebase 前后的远程 HEAD

### 审查循环

- pushback 与依据
- reviewer 故障
- 硬停时：趋势结论、仍开放条目的建议处置、下一步
- retrospective 候选：ADR / CONTEXT / agent instructions / follow-up
