# 委派 prompt 模板

所有路径都传绝对路径。Herdr 委派另按 [herdr-teammate.md](herdr-teammate.md) 附加寻址上下文。

## Implementer

```text
你是 afk-team-workflow 批次中 issue #<N> 的 implementer。读 <skill>/references/implementer.md 并按契约工作。
输入：repo-root=<path>；stage-branch=<branch>；issue-branch=issue/<N>；handoff=<repo-root>/.afk/<batch-id>/handoff-<N>.md。
```

改动面大时可附加：`开工先委派独立探索 agent 勘察。`

## Local reviewer

```text
你是 afk-team-workflow 批次中 issue #<N> 的 local-reviewer，未参与该 issue 实现。读 <skill>/references/local-reviewer.md 并按契约工作。
输入：worktree=<path>；issue-branch=issue/<N>；stage-branch=<branch>；base=<SHA>；handoff=<repo-root>/.afk/<batch-id>/handoff-<N>.md。
```

## Review looper

```text
你负责 afk-team-workflow 批次 stage <K> 的 AI review loop。读 <skill>/references/review-looper.md 并按契约工作。
输入：PR=#<M>；stage-branch=<branch>；worktree=<path>；issues=<N,...>；handoffs=<repo-root>/.afk/<batch-id>/；stage-handoff=<repo-root>/.afk/<batch-id>/handoff-stage-<K>.md。
```

任一 issue 在远程 stage branch 留下 commit 前失效，即丢弃现场并从最新 stage tip 重新委派；不传递半成品接管 prompt。
