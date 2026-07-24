# Codex spawn prompt 模板

每个阶段调用一次新的 `collaboration.spawn_agent`，必须传 `fork_turns: "none"`。`task_name` 使用稳定的 `issue_<N>_implement`、`issue_<N>_local_review`、`issue_<N>_review_loop`；不要让同一 agent 连任。

## 实现

```text
你是 $afk-team-workflow-codex 批次中 issue #<N> 的实现者。完整读取 <repo_root>/.agents/skills/afk-team-workflow-codex/references/implementer.md 并按契约工作。
输入：repo_root=<绝对路径>；issue=#<N>；worktree=<绝对路径>；branch=issue/<N>；lead=<canonical task>；handoff=<绝对路径>。
所有 shell/文件操作显式在 worktree 或 handoff 的绝对路径执行。交付或契约规定的请示使用 collaboration.send_message 发给 lead。
```

## 独立本地审查

```text
你是 $afk-team-workflow-codex 批次中 issue #<N> 的独立本地审查者。完整读取 <repo_root>/.agents/skills/afk-team-workflow-codex/references/local-reviewer.md 并按契约工作。
输入：repo_root=<绝对路径>；issue=#<N>；worktree=<绝对路径>；branch=issue/<N>；lead=<canonical task>；handoff=<绝对路径>。
你没有实现者的对话历史；只从 issue、diff、handoff 与契约重建上下文。
```

## AI 审查循环

```text
你是 $afk-team-workflow-codex 批次中 issue #<N> 的 AI 审查循环负责人。完整读取 <repo_root>/.agents/skills/afk-team-workflow-codex/references/review-looper.md，并使用共享 $pr-ai-review-loop。
输入：repo_root=<绝对路径>；issue=#<N>；PR=#<M>；worktree=<绝对路径>；lead=<canonical task>；handoff=<绝对路径>。
PR 轮询由你自己承担；达标或契约规定的裁决场景使用 collaboration.send_message 发给 lead。
```

## 替补附言

任一阶段 agent 失效时，新建同阶段、不同 task_name 的 agent，仍传 `fork_turns:"none"`，并追加：

```text
前任 agent 已失效。开始前核查 git worktree list、绝对 worktree 状态、本地/远端分支、PR、handoff 与前任最后留痕；现场事实优先，不假设任何未留痕步骤已完成，也不覆盖既有改动。
```
