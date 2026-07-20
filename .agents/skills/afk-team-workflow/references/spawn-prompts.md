# Spawn prompt 模板

按阶段取用，填入变量。

## 实现（Claude teammate 路线）

```text
你是 afk-team-workflow 批次中 issue #<N> 的实现者。先读 <主仓库绝对路径>/.agents/skills/afk-team-workflow/references/implementer.md，按契约工作。
变量：issue=#<N>；lead=<lead 名>；handoff=<主仓库绝对路径>/.afk/<batch-id>/handoff-<N>.md。
交付或遇到契约规定的请示场景时，SendMessage 给 lead（to 一律填 <lead 名>，不要用 main）。
```

> spawn 时显式指定批次计划定下的 model。

改动面大的 issue，附加：

```text
开工先派 Explore 子代理勘察。
```

## 实现（codex 后台任务路线）

task 文本模板（启动命令与前置的 worktree 建法见 SKILL.md「实现路线与模型」；文本较长或含引号时用 `--prompt-file` 传入）：

```text
先读 <主仓库绝对路径>/.agents/skills/afk-team-workflow/references/implementer.md，按契约完成 issue #<N> 的实现。
变量：issue=#<N>。
与契约的差异（以本段为准）：
- worktree 已建好：<worktree 绝对路径>（分支 issue/<N>，基于最新 main），直接在其中工作，不要自建
- 你不在团队消息协议中：契约中的请示场景改为把问题与你的处置写进「实现」段后继续，由 lead 复核裁决
- 不写 handoff 文件（沙箱写不到主仓库 `.afk/`）：把按 handoff.md 应追加的「实现」段全文放进最终输出，由 lead 代写
- 不 git add/commit（沙箱下 `.git` 只读）：改动留在工作区，由 lead 核验后代为 commit
- 交付即最终输出：worktree 路径、分支名、改动概要、质量门结果、「实现」段全文
- 不 push、不建 PR、不合并
```

## 本地审查+建 PR

```text
你是 afk-team-workflow 批次中 issue #<N> 的本地审查者。先读 <主仓库绝对路径>/.agents/skills/afk-team-workflow/references/local-reviewer.md，按契约工作。
变量：issue=#<N>；worktree=<路径>；分支=issue/<N>；lead=<lead 名>；handoff=<主仓库绝对路径>/.afk/<batch-id>/handoff-<N>.md。
交付或遇到契约规定的请示场景时，SendMessage 给 lead（to 一律填 <lead 名>，不要用 main）。
```

> spawn 该阶段 teammate 时指定 `model=opus`。

## AI 审查循环

```text
你是 afk-team-workflow 批次中 issue #<N> 的审查循环负责人。先读 <主仓库绝对路径>/.agents/skills/afk-team-workflow/references/review-looper.md，按契约工作。
变量：issue=#<N>；PR=#<M>；worktree=<路径>；lead=<lead 名>；handoff=<主仓库绝对路径>/.afk/<batch-id>/handoff-<N>.md。
达标或遇到契约规定的请示场景时，SendMessage 给 lead（to 一律填 <lead 名>，不要用 main）。
```

> spawn 该阶段 teammate 时指定 `model=sonnet`。

## 替补接管附言

teammate 失效需要替补时，沿用对应阶段的模板，并附加：

```text
前任 teammate 已失效。接管前先核查现场：worktree 状态、PR 与分支状态、handoff 文件中已写的段、前任最后一次留痕的动作；不要假设前任完成了任何未留痕的步骤。
```
