---
name: afk-team-workflow-codex
description: 仅在用户显式调用 `$afk-team-workflow-codex` 时使用：用 Codex lead 与隔离上下文子代理，把一个 Spec 的全部子 issue 或一组显式 issue 无人值守推进到全部合并或明确搁置。
---

# AFK 团队执行流程（Codex）

担任 lead：只负责调度、Git/worktree 编排、合并、裁决、健康检查、恢复与收尾；把业务实现、本地审查和 PR AI 审查循环交给三个不同的 Codex 子代理。以 GitHub 与 Git 现场为唯一远端真相，以 `.afk/` 保存无法重推的薄账本与阶段交接。

设 `repo_root` 为主 checkout 的绝对路径。所有 lead 命令显式使用 `repo_root`；lead 只按契约写 `.afk/<batch-id>.jsonl`、集中管理 issue worktree，并创建和清理 preflight 临时产物，不写业务代码。所有 teammate shell 命令显式使用传入的绝对 worktree 路径；teammate 文件写入只允许落在 worktree，或契约传入的绝对 handoff 路径。内部 teammate 一律使用 collaboration 子代理；不要创建用户可见的新 Codex task。

## 1. 先判定恢复，再过硬启动门槛

1. 在 `repo_root/.afk/*.jsonl` 中找末条 `kind` 不是 `closed` 的账本。存在任一未关闭批次时，先完整读取 [recovery.md](references/recovery.md)，完成接管/重开/忽略分流；不能把它覆盖成新批次。缺少 `runtime` 的旧行按 legacy/Claude 处理，`runtime` 不参与终态判定。
2. 对新批次或选择接管的批次，完整执行 [preflight.md](references/preflight.md)。能力与权限探针全部通过，才可制定批次计划；任一项失败就响亮停止并在用户仍在线时解决，不能进入 AFK 后再等待权限弹窗。

preflight 必须只读证明 squash merge 配置与分支规则不会阻断已授权的自动合并路径；本地探针兼容旧版 Git，临时 push ref 沿用真实 `issue/` 分支命名空间，并在任意退出路径清理 worktree 与 Codex probe 临时目录。具体探针与测试口径以引用页和脚本为准。

完成条件：已证明不存在待处理旧批次，或用户明确选择了恢复动作；且 preflight 的每项均为 PASS。

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

用户确认后，从 `repo_root` 调用本 skill 的 `scripts/ledger.sh`：首条 `decision` 携带 `--scope-spec` 或 `--scope-issues`，第二条 `authorization` 记录两项授权。所有新行自动含 `"runtime":"codex"`。

完成条件：用户已明确回复授权范围，且账本首两行可被 `jq` 解析、首行带 scope。

## 4. 建立 lead 运行面并启动三阶段接力

先完整读取 [lead-operations.md](references/lead-operations.md)、[spawn-prompts.md](references/spawn-prompts.md) 与 [handoff.md](references/handoff.md)。用 `codex_app__automation_update` 为当前任务创建约 30 分钟一次的 heartbeat，prompt 要求 lead 跑全批 `batch-poll` 和 agent 健康检查；保存 automation id，批次关闭或搁置完成后删除。

lead 从最新 `origin/main` 集中创建 worktree：

```text
<repo_root>/.worktrees/afk-codex/<batch-id>/issue-<N>
```

分支固定为 `issue/<N>`。先检查 `git worktree list`、本地分支与远端分支；只为确定是全新现场的 issue 创建，不覆盖已有现场。每个 issue 三阶段严格串行：

| 阶段 | 契约 | 交付物 |
|---|---|---|
| 实现 | [implementer.md](references/implementer.md) | 已 commit、质量门通过、未 push/未建 PR 的 worktree |
| 独立本地审查 | [local-reviewer.md](references/local-reviewer.md) | 已修复有效发现、已 push、已建非 draft PR |
| AI 审查循环 | [review-looper.md](references/review-looper.md) | 当前 HEAD 达标报告与复盘候选 |

每个阶段都调用 `collaboration.spawn_agent` 新建不同 agent，并显式传 `fork_turns: "none"`。agent 只从阶段 prompt、issue/PR、绝对 worktree、契约与 `.afk/<batch-id>/handoff-<N>.md` 重建上下文；不让同一 agent 连任阶段。只有前一阶段交付物经过 lead 核验后才 spawn 下一阶段。

完成条件：每个在途 issue 恰有一个阶段 agent，活跃 issue 数不超并发上限，且每个 agent 都拿到绝对路径和 `fork_turns:"none"`。

## 5. 调度到终态

按 [lead-operations.md](references/lead-operations.md) 执行依赖补位、定时健康检查、替补、裁决、一次一笔合并、gap 浮现与清尾。协作工具映射固定为：

- 新阶段或替补：`collaboration.spawn_agent(..., fork_turns="none")`
- 运行中定向消息：`collaboration.send_message`
- idle agent 的同阶段下一 turn：`collaboration.followup_task`
- 状态检查：`collaboration.list_agents`
- 停止失效 agent：`collaboration.interrupt_agent`，先核查现场再 spawn 替补
- teammate 等待邮箱：`collaboration.wait_agent`

review teammate 自己承担分钟级 PR 轮询；lead heartbeat 只做约 30 分钟的全批健康检查。常规 idle 不是故障，也不触发即时 batch-poll。

完成条件：所有计划成员均已合并或明确搁置，且所有不可重推裁决均已追加到账本。

## 6. 清尾并关闭

按 lead 契约完成单轮清尾分拣、Spec QA 评论、worktree/本地分支清理、heartbeat 删除和最终汇报。最后从 `repo_root` 追加 `closed` 行；保留 `.afk/<batch-id>.jsonl` 与 handoff 作为审计和跨运行时恢复源，不提交它们。

完成条件：没有活跃 teammate、没有本批遗留 heartbeat/worktree/本地 issue 分支，账本末条为 `closed`，用户收到已合并、needs-human、跳过/未启动及四类复盘候选清单。
