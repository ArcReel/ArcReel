# Spawn prompt 模板

按阶段取用，填入变量。契约文件路径必须填**主仓库的绝对路径**：teammate 的 worktree 基于 origin/main 检出，未必包含（最新的）skill 文件。

## 实现

```text
你是 afk-team-workflow 批次中 issue #<N> 的实现者。先读 <主仓库绝对路径>/.agents/skills/afk-team-workflow/references/implementer.md，按契约工作。
变量：issue=#<N>；lead=<lead 名>。
交付或遇到契约规定的请示场景时，SendMessage 给 lead。
```

## 本地审查+建 PR

```text
你是 afk-team-workflow 批次中 issue #<N> 的本地审查者。先读 <主仓库绝对路径>/.agents/skills/afk-team-workflow/references/local-reviewer.md，按契约工作。
变量：issue=#<N>；worktree=<路径>；分支=issue/<N>；lead=<lead 名>。
交付或遇到契约规定的请示场景时，SendMessage 给 lead。
```

## AI 审查循环

```text
你是 afk-team-workflow 批次中 issue #<N> 的审查循环负责人。先读 <主仓库绝对路径>/.agents/skills/afk-team-workflow/references/review-looper.md，按契约工作。
变量：issue=#<N>；PR=#<M>；lead=<lead 名>。
达标或遇到契约规定的请示场景时，SendMessage 给 lead。
```

## 替补接管附言

teammate 失效需要替补时，沿用对应阶段的模板，并附加：

```text
前任 teammate 已失效。接管前先核查现场：worktree 状态、PR 与分支状态、前任最后一次留痕的动作；不要假设前任完成了任何未留痕的步骤。
```
