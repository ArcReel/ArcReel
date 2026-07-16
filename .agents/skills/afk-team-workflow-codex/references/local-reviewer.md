# 独立本地审查与建 PR 契约（Codex 第二阶段）

输入：`repo_root`、issue 号、绝对 worktree、分支、lead canonical task 名、绝对 handoff。

你没有实现阶段对话历史。所有命令显式在传入的 worktree 中运行；主 checkout 的唯一写入例外是向传入的绝对 handoff 路径追加本阶段记录。交付一个已修复有效发现、已验证、已 push、已建非 draft PR 的分支。

## 步骤

1. 通读 issue 正文/评论、实现 handoff 和完整 diff，固定 `origin/main` 为审查 fixed point、issue 为 spec source。
2. 完整使用外部 `$code-review`，保持其 Standards / Spec 双轴报告分离。逐条核验报告，修复有效发现；接近重做规模的事项用 `collaboration.send_message` 请示 lead，对不成立项保留技术依据。
3. commit 审查修复并重新运行相关质量门。push 前再次 `git fetch origin`；main 已前进时 rebase 到最新 `origin/main`，解决冲突后必须以新的 `origin/main` 再次使用 `$code-review` 并执行质量门。只有最终 diff 的审查和验证均通过才进入下一步。
4. push `issue/<N>`。GitHub 状态变更优先使用已通过 preflight 的 connector 创建非 draft PR；标题遵循仓库 Conventional Commits，正文含 `Closes #<N>` 与验证说明。
5. 复核 PR head 与本地 HEAD 一致，PR 不是 draft。

## 交付

在 handoff 追加“### 本地审查”段，明确记录 PR 号、reviewed HEAD、远端 HEAD 核验与最终质量门，再用 `collaboration.send_message` 向 lead 报告 PR 号、HEAD、审查发现、修复和验证。结束本 agent，保留 worktree。

完成条件：PR 已存在且非 draft，远端 HEAD 等于已验证的本地 HEAD，handoff 包含审查取舍、PR、reviewed HEAD 与质量门证据。
