# 接管未收尾批次

只在新 team-lead 需从持久事实接管，或用户明确要求恢复 / 重新对账时读本页。同一会话的续跑直接沿用当前运行上下文。

1. 读 `.afk/<batch-id>.jsonl`，将 `membership` 折入 scope，并查询远程 issues、stage PR 与 branch；issue labels 是暂停状态的 remote truth，ledger 只补充原因。
2. 停止旧 agents，清理本批 stale local worktrees / branches 与未集成 handoff 文件。
3. 以 remote truth 续接：merged stage 进入下一 stage；open PR 从其远程 HEAD 重建 stage worktree并续接 review loop；其余从远程 stage branch 的 `Refs #<N>` commits 得到已完成 issues，远程没有的改动均视为未完成。
4. 前任 transcript 中的合并授权不可继承；新 team-lead 在首次合并前重新请求未完成 stage 的授权。

用户选择重开时，关闭在途 stage PR，停止本批 agents，清理本批 workspace / worktrees / branches，append `closed`，然后使用新 `batch-id`。
