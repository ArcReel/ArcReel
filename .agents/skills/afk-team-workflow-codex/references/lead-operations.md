# Lead 运行契约

本页在用户完成前置授权后加载。lead 不写业务代码；所有账本命令以主 checkout `repo_root` 为 workdir，所有 issue 命令以绝对 worktree 为 workdir。

## Worktree 与依赖补位

新 issue 只从最新 `origin/main` 启动：先 `git fetch origin`，再检查 `git worktree list --porcelain`、`refs/heads/issue/<N>`、`refs/remotes/origin/issue/<N>` 和 PR。确认没有现场后，创建 `<repo_root>/.worktrees/afk-codex/<batch-id>/issue-<N>` 与分支 `issue/<N>`。lead 集中管理 worktree；teammate 不创建、移动或删除它。

只有全部 blocker 已合入 main 才启动。issue 到达“已合并/已搁置”终态才释放并发槽，随后按依赖 frontier 补位。blocker 搁置时，下游保持未启动并进入收尾清单。

恢复或替补遇到既有 worktree/分支时，以现场为准复用；路径被另一 worktree 占用、分支 diverge 或存在未提交改动时先记录并让替补核查，绝不 force 覆盖。

## Heartbeat 健康检查

当前 task 的约 30 分钟 heartbeat 只唤醒 lead。每次唤醒：

1. 跑共享 `batch-poll.sh` 获取全批 `stage_hint`、PR `updatedAt`/`mergeable`、`conflicting` 与 `merge_candidate`。
2. 调用 `collaboration.list_agents`，把 agent 状态与远端更新时间、最近交付/请示合并判断。
3. 常规 idle 无动作；reviewer 等待是合理停顿。仅当长时间无远端/本地进展且没有合理等待理由时，先 `collaboration.send_message` 询问。
4. 仍无回应时 `collaboration.interrupt_agent`，核查 worktree、分支、PR 和 handoff 后，按 spawn 模板创建 `fork_turns:"none"` 的替补。
5. 对 idle agent 需要继续同一阶段的新 turn 时使用 `collaboration.followup_task`；运行中消息使用 `collaboration.send_message`。

review teammate 自持 60/120/180 秒轮询，lead 不集中代 poll，也不为它创建 heartbeat。避免批次 heartbeat 重入：上一次健康检查未结束时，本次只记录并退出。

## 合并纪律

一次只合一笔。合并前必须同时取得：

- review-looper 对达标 HEAD 的报告；
- `gh pr view <M> --json mergeable,headRefOid` 的远端事实，`mergeable` 为 `MERGEABLE` 且 `headRefOid` 等于报告 HEAD；
- PR 仍 open、非 draft，无未裁决的 reviewer 冲突。

分支落后 main 但无冲突不阻塞合并。优先用 GitHub connector 按 PR 标题 squash merge；成功后用远端事实复核，再追加 ledger `merge`。不要广播 main 前进，teammate 在下次修复 push 或冲突出现时自行 rebase。

## Lead 裁决

teammate 的暂停场景只到 lead：

1. **故障**：bot 报错、quota、长时间无响应或意外权限失败。按共享 review-loop 建议重试一次；仍失败就对本 PR 停用该 reviewer，追加 `fault`，收尾前可补审一次。AFK 中新的权限失败也按 fault 安全搁置/转呈，不绕过授权。
2. **重复意见**：同一 reviewer 重提已有 pushback 的主题，维持技术结论，让 looper 引用在案依据回复后继续；值得升级的原则记复盘候选。
3. **真实 reviewer 冲突或业务取舍**：不选边。用 connector 把 PR 转 draft、issue 改为 `ready-for-human`、在 PR 评论双方立场，追加 `shelve`；结束 teammate，保留远端分支/PR供人工接手，清理本地 worktree/分支。
4. **收敛兜底**：共享 review-loop 达到 8 轮，或连续两轮只有 nit/format push 时，不替用户选择强行合并、继续或放弃。用 connector 把 PR 转 draft、issue 改为 `ready-for-human`，在 PR 评论触发条件、当前未满足门槛与三个可选动作，追加 `shelve`；结束 teammate，保留远端分支/PR 供人工接手，清理本地 worktree/分支。

## Gap 与清尾轮

功能性 gap 指 Spec 有要求但所有子 issue 都未覆盖。发现时追加 `gap`，向用户发非阻塞更新说明缺口、建议与影响；批次继续。只有用户中途明确授权才用 `$to-tickets` 立项并加入依赖图。清尾授权不覆盖功能性 gap。

全部计划成员终态后只做一轮清尾：聚合 ledger 与 handoff 的 follow-up，逐条要求同时满足：

1. 有实证的真实缺陷；
2. 本批引入/遗留，或阻碍 Spec 验收；
3. 修复方向唯一明确、无业务取舍。

满足者在持有清尾授权时用 `$to-tickets` 立项为 Spec sub-issue，保持 `## Blocked by` 格式，并走同样三阶段；不满足者转呈。清尾轮新滋生的候选全部转呈，不开第二轮。把分拣结果追加 `decision`；显式 issue 批次扩员后再追加一条带新 scope 的 `decision`。

## 收口与清理

1. 用 connector 在共同 Spec 发布人工 QA 清单，不关闭 Spec。按已合并 issue 给 PR 链接和真实操作路径；末尾列搁置、跳过/未启动和 gap。无共同 Spec 时并入最终汇报。
2. 确认 agent 已结束；删除已合并或已搁置 issue 的 worktree与本地 `issue/<N>` 分支。只有远端已证实终态且路径属于本批时才清理。合并后因 squash 无法 `git branch -d` 时，可在该验证后删除本地分支；远端待人工接手分支保留。
3. 用 `codex_app__automation_update` 删除本批 heartbeat，确认 id 不再存在，避免幽灵唤醒。
4. 汇报三份清单：已合并（issue/PR）、needs-human（争点）、跳过/未启动（原因）；再附 gap、故障、清尾转呈与聚合复盘四类候选。候选为空是正常结果。
5. 从 `repo_root` 调用本 skill 的 ledger 追加 `closed`。保留账本与 handoff，不提交 `.afk/`。

终态检查：`collaboration.list_agents` 无本批活跃 agent，`git worktree list` 无本批路径，heartbeat 已删除，ledger 末条 `kind == "closed"`。
