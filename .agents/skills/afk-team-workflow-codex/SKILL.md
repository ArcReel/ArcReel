---
name: afk-team-workflow-codex
description: 用 Codex lead 与隔离上下文子代理，把一个 Spec 的全部子 issue 或一组显式 issue 无人值守推进到全部合并或明确搁置。
disable-model-invocation: true
---

# AFK 团队执行流程（Codex）

担任 lead：只负责调度、Git/worktree 编排、合并、裁决、健康检查、恢复与收尾；把业务实现、本地审查和 PR AI 审查循环交给三个不同的 Codex 子代理。以 GitHub 与 Git 现场为唯一远端真相，以 `.afk/` 保存无法重推的薄账本与阶段交接。

设 `repo_root` 为主 checkout 的绝对路径。所有 lead 命令显式使用 `repo_root`；lead 只按契约写 `.afk/<batch-id>.jsonl`、集中管理 issue worktree，并创建和清理 preflight 临时产物，不写业务代码。所有 teammate shell 命令显式使用传入的绝对 worktree 路径；teammate 文件写入只允许落在 worktree，或契约传入的绝对 handoff 路径。内部 teammate 一律使用 collaboration 子代理；不要创建用户可见的新 Codex task。

## 1. 先判定恢复，再过硬启动门槛

1. 在 `repo_root/.afk/*.jsonl` 中找末条 `kind` 不是 `closed` 的账本。存在任一未关闭批次时，先完整读取 [recovery.md](references/recovery.md)，完成接管/重开/忽略分流；保留旧批次现场。
2. 对新批次或选择接管的批次，完整执行 [preflight.md](references/preflight.md)。能力与权限探针全部通过，才可制定批次计划；任一项失败就响亮停止并在用户仍在线时解决，不能进入 AFK 后再等待权限弹窗。

完成条件：若用户选择忽略，现场保持不变且本次运行停止；否则已证明不存在待处理旧批次，或用户明确选择了接管/重开，且 preflight 的每项均为 PASS。

## 2. 重建批次事实与语义

先运行共享机械探针，不复制它：

```bash
bash <repo_root>/.agents/skills/afk-team-workflow/scripts/batch-poll.sh --spec <N>
bash <repo_root>/.agents/skills/afk-team-workflow/scripts/batch-poll.sh --issues 1,2,3
```

随后逐个通读 issue 正文与评论，核对验收边界、隐含取舍，以及机械解析的 `blocked_by`。按以下规则分流：

- `ready-for-agent` 进入批次；`ready-for-human` 及其下游阻塞链不启动。
- 无 triage 标签时按正文判断，不把 `ready_to_start` 当 triage 结论。
- 依赖只有在 blocker 已合入 `main` 后才解除；同一改动域或足迹重叠的 issue 串行。
- 默认 issue 并发上限为 3；实际值取用户上限与“当前运行时除 lead 外的可用 agent 槽位”中的较小值。issue 处于实现、本地审查或 review-loop 任一阶段都占一个槽。
- 恢复中的 `stage_hint` 按 recovery 契约，不按新批次覆盖。

完成条件：每个成员都已标出依赖、triage、改动域、当前阶段和“启动/跳过/等待”结论。

## 3. 展示计划并请求唯一业务授权

向用户展示成员清单、依赖顺序、跳过项及连带下游、并发上限。随后只请求一次两项预批：

1. 本批所有 PR（含清尾轮 PR）的 squash 合并；
2. 对符合收尾判据的缺陷类 follow-up 自主立项并跑到合并。

同时声明自动动作边界：可修改 triage 标签、把冲突 PR 转 draft、在 Spec 发布 QA 验收评论；清尾授权之外不创建新 issue，功能性 gap 仍需用户中途指令。用户确认前不写账本、不建 worktree、不 spawn teammate。恢复会话必须重新授权，旧 `authorization` 行只作历史参考。

用户确认后，从 `repo_root` 调用共享 `.agents/skills/afk-team-workflow/scripts/ledger.sh`：首条 `decision` 携带 `--scope-spec` 或 `--scope-issues`，第二条 `authorization` 记录两项授权。随后按 lead 契约创建当前 task 的正式 heartbeat；创建失败就停止，不能 spawn teammate。

完成条件：用户已明确回复授权范围，账本首两行可被 `jq` 解析、首行带 scope，且正式 heartbeat 已创建并登记。

## 4. 建立 lead 运行面并启动三阶段接力

完整读取 [lead-operations.md](references/lead-operations.md)、[spawn-prompts.md](references/spawn-prompts.md) 与 [handoff.md](references/handoff.md)，由它们作为 heartbeat、worktree、槽位预算、spawn 和交接的单一真相源。每个 issue 三阶段严格串行：

| 阶段 | 契约 | 交付物 |
|---|---|---|
| 实现 | [implementer.md](references/implementer.md) | 已 commit、质量门通过、未 push/未建 PR 的 worktree |
| 独立本地审查 | [local-reviewer.md](references/local-reviewer.md) | 已修复有效发现、已 push、已建非 draft PR |
| AI 审查循环 | [review-looper.md](references/review-looper.md) | 当前 HEAD 达标报告与复盘候选 |

完成条件：每个在途 issue 恰有一个阶段 agent，活跃 issue 数与 agent 槽位均符合 lead 契约，且只有前一阶段交付物核验通过后才启动下一阶段。

## 5. 调度到终态

按 [lead-operations.md](references/lead-operations.md) 执行依赖补位、定时健康检查、替补、裁决、一次一笔合并、gap 浮现与清尾。

完成条件：所有计划成员均已合并或明确搁置，且所有不可重推裁决均已追加到账本。

## 6. 清尾并关闭

按 lead 契约完成单轮清尾分拣、Spec QA 评论、worktree/本地分支清理、heartbeat 删除和最终汇报。最后从 `repo_root` 追加 `closed` 行；保留 `.afk/<batch-id>.jsonl` 与 handoff 作为审计和跨运行时恢复源，不提交它们。

完成条件：没有活跃 teammate、没有本批遗留 heartbeat/worktree/本地 issue 分支，账本末条为 `closed`，用户收到已合并、needs-human、跳过/未启动及四类复盘候选清单。
