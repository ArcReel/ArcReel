# 崩溃恢复：接管未完成批次

SKILL.md 入口扫描发现 `.afk/` 下存在缺少 `closed` 收尾行的账本时加载本契约——上一会话的 lead 可能中途终止。gh/git 是唯一真相：恢复即 replay 账本补回不可从远端重推的事实，再以一次 poll 对账，而非重建状态机。

## 1. 对账并询问用户

对该 batch-id 跑一次 batch-poll：`spec-<N>` 批次直接 `--spec <N>`；slug 批次的成员取账本**最后一条带 `scope` 的行**（清尾扩员会追加 scope 行），据此 `--issues`；无任何 scope 行时须人工指定范围。

- 所有 issue 的 `stage_hint` 均为 `done` / `shelved` → 远端已收敛，但前任的本地收尾未必完成：对路线为 codex 的 issue 先在其 worktree 跑 companion `cancel` 停掉仍存活的任务，再按 SKILL.md 收尾节执行完整收尾（含 worktree 清理）并补 `closed` 行
- 存在非终态 issue → 向用户列出批次标识与终态/在途分布，三选一：**接管**（走下方 §2–§3）/ **重开**（先 cancel 在途 codex 任务并清理批次 worktree 与分支，再弃现状回 SKILL.md 第一步）/ **忽略**（一切不动）

## 2. Replay 账本

读 `.afk/<batch-id>.jsonl` 补回 poll 看不到的历史（各 `kind` 含义见 SKILL.md 账本节）并沿用：已定裁决不重新决策、已吸收故障不重复处置、已搁置事项不重复动作。另读各 issue 的 handoff 文件。两条规则：

- **对账以 poll 为准**：账本记历史，poll 记现实——账本有 `merge` 而 PR 仍 OPEN，按未合并处理
- **`authorization` 行不等于已授权**：前置授权写在前任 transcript 中，新会话无法继承。执行任何合并前按 SKILL.md 前置授权步骤重新征求；已持久化到本地配置的授权（属配置而非 transcript 记忆）除外

## 3. 对非终态 issue 重 spawn

前任 lead 终止后其 teammate 不可达也不可问责，一律重 spawn 替补——假死 teammate 与新上下文并发驱动同一 PR 比重复劳动更糟。按 `stage_hint` 重 spawn 对应阶段（`no-branch`→实现、`local-review`→本地审查、`review-loop`→审查循环），使用 spawn-prompts.md 的替补接管附言。两个特例：

- `review-loop`：poll 显示该 PR `updatedAt` 近期仍在变动时，先观察一个唤醒周期再重 spawn，避免两个上下文同时推同一 PR
- `no-branch` 且路线为 codex：codex 后台任务不随 lead 会话终止而死。先看 worktree：HEAD 有 `origin/main` 之外的 commit（前任已代 commit）→ 实现已交付，直接重 spawn 本地审查阶段；否则在对应 worktree 跑 companion（codex 插件的 `codex-companion.mjs`）`status --all` 分三态处置：已完成 → 按 SKILL.md「实现路线与模型」以 `result` 交付并核验，不重跑；在途且有进展 → 等待其完成；失败或停滞 → 按 SKILL.md 健康检查节的替补处置执行
