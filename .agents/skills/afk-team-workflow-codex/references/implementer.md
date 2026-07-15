# 实现契约（Codex 第一阶段）

输入：`repo_root`、issue 号、绝对 worktree 路径、分支 `issue/<N>`、lead 的 canonical task 名、绝对 handoff 路径。

交付一个基于最新 `origin/main`、改动全部 commit、质量门通过、未 push 且未建 PR 的 worktree。lead 已创建 worktree；所有命令显式以该绝对路径为 `workdir`，不切换或修改主 checkout。

## 步骤

1. 通读 `AGENTS.md`、issue 正文与评论、已有 handoff。验收标准就是批准边界；正文与代码现实冲突或存在重大接口取舍时，用 `collaboration.send_message` 请示 lead。
2. 核对当前 worktree 路径、分支名和基线；若与 prompt 不符立即报 lead，不自行重建 worktree。需要 server/数据库时使用与其他 issue 隔离的端口和数据目录。
3. 可行处使用 `$tdd`，按 issue 已批准的公共 seam 做纵向 red→green；只有超出验收范围的 seam/接口取舍才请示 lead。
4. 运行项目相关质量门：测试、lint、类型检查；涉及前端则含 `pnpm lint && pnpm check`。formatter 改写也要纳入 commit。
5. 检查 `git status` 只剩干净 worktree，并确认本阶段未 push、未创建 PR。

## 交付

用补丁在 handoff 末尾追加“### 实现”段。然后 `collaboration.send_message` 给 lead：worktree、分支、改动概要、commit、质量门和环境备案。发送后结束本 agent；保留 worktree给下一阶段。

完成条件：lead 能在传入路径看到干净的 `issue/<N>`、全部改动已有 commit、质量门证据完整且远端没有新 PR。
