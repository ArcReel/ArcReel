# 接管未收尾批次

只在新 team-lead 需从持久事实接管，或用户明确要求恢复 / 重新对账时读本页。同一会话的续跑直接沿用当前运行上下文。

1. 读 `.afk/<batch-id>.jsonl` 取回 scope、stage 计划、裁决与故障，并查询远程 stage PR / branch。已合并 stage 以 GitHub 为准。
2. 对当前 stage，从远程 stage branch 的 `Refs #<N>` commits 得到已完成 issues。远程没有的改动全部视为未完成，不接续半成品。
3. 停止旧 agents，清理未完成 issues 的 worktrees 与本地 branches，再从远程 stage branch 最新提交重新调度剩余 frontier。
4. 前任 transcript 中的合并授权不可继承；新 team-lead 在首次合并前重新请求未完成 stage 的授权。

用户选择重开时，关闭在途 stage PR，停止本批 agents，清理本批 workspace / worktrees / branches，append `closed`，然后使用新 `batch-id`。
