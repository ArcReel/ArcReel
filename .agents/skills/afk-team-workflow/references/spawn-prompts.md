# 委派 prompt 模板

按阶段取用，填入变量。

## 实现

```text
你是 afk-team-workflow 批次中 issue #<N> 的实现者。先读 <skill 目录绝对路径>/references/implementer.md，按契约工作。
变量：issue=#<N>；worktree=<路径>；分支=issue/<N>；handoff=<主仓库绝对路径>/.afk/<batch-id>/handoff-<N>.md。
所有路径按绝对路径字面解释：除读取契约与追加 handoff 外，一切文件读写都限定在 worktree；任务若要修改契约文件本身，也必须修改 worktree 内的副本，经分支与 PR 交付。
交付或遇到契约规定的请示场景时，联系 team-lead。
```

> 委派时使用批次计划按 [model-selection.md](model-selection.md) 定下的模型。

改动面大的 issue，附加：

```text
开工先委派独立探索 agent 勘察。
```

## 本地审查+建 PR

```text
你是 afk-team-workflow 批次中 issue #<N> 的本地审查者。先读 <skill 目录绝对路径>/references/local-reviewer.md，按契约工作。
变量：issue=#<N>；worktree=<路径>；分支=issue/<N>；handoff=<主仓库绝对路径>/.afk/<batch-id>/handoff-<N>.md。
所有路径按绝对路径字面解释：除读取契约与追加 handoff 外，一切文件读写都限定在 worktree；任务若要修改契约文件本身，也必须修改 worktree 内的副本，经分支与 PR 交付。
交付或遇到契约规定的请示场景时，联系 team-lead。
```

> 按 [model-selection.md](model-selection.md) 选择本阶段模型。

## AI 审查循环

```text
你是 afk-team-workflow 批次中 issue #<N> 的审查循环负责人。先读 <skill 目录绝对路径>/references/review-looper.md，按契约工作。
变量：issue=#<N>；PR=#<M>；worktree=<路径>；handoff=<主仓库绝对路径>/.afk/<batch-id>/handoff-<N>.md。
所有路径按绝对路径字面解释：除读取契约与追加 handoff 外，一切文件读写都限定在 worktree；任务若要修改契约文件本身，也必须修改 worktree 内的副本，经分支与 PR 交付。
达标或遇到契约规定的请示场景时，联系 team-lead。
```

> 按 [model-selection.md](model-selection.md) 在委派时选择本阶段模型；选择理由 append 账本 `decision`。

## 替补接管附言

负责 agent 失效需要替补时，沿用对应阶段的模板，并附加：

```text
前任 agent 已失效。接管前先核查现场：worktree 状态、PR 与分支状态、handoff 文件中已写的段、前任最后一次留痕的动作；不要假设前任完成了任何未留痕的步骤。
```
