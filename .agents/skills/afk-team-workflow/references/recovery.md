# 接管未收尾批次

仅当新 team-lead 需要依据持久化事实接管批次，或用户明确要求恢复 / 重新对账时阅读本页。同一会话内的续跑直接沿用当前运行上下文即可。

1. 读 `.afk/<batch-id>.jsonl` 取回 scope，并查询远程 issues、stage PR 与 branch；issue labels 是暂停状态的 remote truth，ledger 只补充原因。
2. 停止旧 agents，清理本批残留的本地 worktrees / branches 与未集成的 handoff 文件。
3. 以 remote truth 续接：merged stage 进入下一 stage；其余从远程 stage branch 的 `Refs #<N>` commits 得到已完成 issues，远程没有的改动均视为未完成，未交付的 fixer 修复同样重做。open PR 从其远程 HEAD 重建 stage worktree，交给新的 review-looper 接力；QA 进度读 PR 上的 QA 结论评论：没有评论时以全量模式重做，有评论时按 SKILL.md 合并前核对的「QA 覆盖该 HEAD」处理。
4. 前任会话记录中的合并授权与费用预算不可继承；新 team-lead 须在首次合并前重新请求未完成 stage 的合并授权，并在部署冒烟前重新请求费用预算。

用户选择重开时，关闭在途 stage PR，停止本批 agents，清理本批创建的 worktrees、本地 branches 和其他运行资源，append `closed`，然后从 SKILL.md 第 1 节重新计划新批次。
