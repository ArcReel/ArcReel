# 独立本地审查与建 PR 契约（Codex 第二阶段）

输入：`repo_root`、issue 号、绝对 worktree、分支、lead canonical task 名、绝对 handoff、通过 preflight 的 `codex_bin`。

你没有实现阶段对话历史。所有命令显式在传入的 worktree 中运行；交付一个已修复有效发现、已验证、已 push、已建非 draft PR 的分支。

## 步骤

1. 通读 issue 正文/评论、实现 handoff 和完整 diff。做规格三问：验收要求有无缺失或只完成一半；改动有无 scope creep；已覆盖项是否实现正确。小缺口就地补齐，接近重做规模的事项用 `collaboration.send_message` 请示 lead。
2. 在 worktree 中运行 `<codex_bin> review --base origin/main`。把输出当外部审查意见，按 `$receiving-code-review` 逐条结合代码和测试验证；修复有效发现，对不成立项保留技术依据。不要触发 GitHub 云端 `@codex review`。
3. commit 审查修复，重新运行相关质量门。然后 `git fetch origin`；main 已前进时 rebase 到最新 `origin/main`，解决冲突并再次验证。
4. push `issue/<N>`。GitHub 状态变更优先使用已通过 preflight 的 connector 创建非 draft PR；标题遵循仓库 Conventional Commits，正文含 `Closes #<N>` 与验证说明。
5. 复核 PR head 与本地 HEAD 一致，PR 不是 draft。

## 交付

在 handoff 追加“### 本地审查”段，再用 `collaboration.send_message` 向 lead 报告 PR 号、HEAD、审查发现、修复和验证。结束本 agent，保留 worktree。

完成条件：PR 已存在且非 draft，远端 HEAD 等于已验证的本地 HEAD，handoff 包含审查取舍。
