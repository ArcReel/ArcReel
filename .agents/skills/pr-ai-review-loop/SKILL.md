---
name: pr-ai-review-loop
description: 无人值守驱动 CodeRabbit、Gemini Code Assist、OpenAI Codex 的 review → 修复 → push → 再 review 循环,直到全部通过或触发收敛退出。主动调用:用户刚 push PR 或跑完 /commit-push-pr;提到 review / coderabbit / gemini / codex / 审查 / AI review / 等 bot 回复;CodeRabbit paused 需 resume;reviewer 有 actionable comments。即使用户只说"PR 怎么样了""review 回了吗"也应触发。
---

# AI Review 自动循环

PR push 之后,CodeRabbit、Gemini、Codex 三家 AI reviewer 会反复进行 review → 修复 → push → 再 review 的循环。本 skill 负责调度该循环:监控状态、必要时触发 review、收集所有意见后转交 `receiving-code-review` 处理。

## 运行模式:无人值守与两类暂停场景

自动执行整个循环,无需每轮征求授权。触发命令、push 修复、回应 inline、下一轮 poll 的延迟均按下文决策表自行决定。下列场景需要暂停并询问用户。

### A. 故障类暂停

- bot 报错(如 "Internal error"、"Token limit exceeded")
- 某家 reviewer 超过 15 分钟未响应
- `gh` 401/403 认证失败
- `poll.sh` 或 `classify_commits.sh` 重试一次后仍报错
- review 意见语义模糊,`receiving-code-review` 无法判定是否 pushback

### B. 调度类暂停

- **根本性分歧无定论**:同一主题指纹(reviewer + 关键词,例如 "Pydantic `extra=ignore` vs `forbid`")被同一家 reviewer 连续提出 ≥ 3 轮,且无 ADR / memory 兜底。暂停并请用户决定是否升级 ADR(与「收敛兜底」#3 同口径)
- **reviewer 之间冲突**:同一议题,A 家主张 X、B 家反对 X。暂停并交用户裁决,不自行选边
- **业务取舍**:修复方案在前向兼容、性能、用户体验上存在显著差异,可能影响业务意图。暂停并确认

主题指纹由 Claude 在对话上下文中维护 `topic_history`,以语义相似度判定同主题。

## 前置条件

- 当前分支已有对应 PR(`gh pr view` 能读取到 PR 号)。若无,建议先运行 `/commit-commands:commit-push-pr`
- `gh` 已登录且具备评论权限(`gh auth status` 通过)
- 仓库已接入 CodeRabbit、Gemini Code Assist、OpenAI Codex 三家 reviewer
- 已安装 `jq`(macOS:`brew install jq`;Debian/Ubuntu:`apt-get install jq`;Fedora:`dnf install jq`;Windows:WSL 内安装)

## 三家 reviewer 速查

参见 [references/reviewers.md](references/reviewers.md):bot 名(GraphQL 与 REST 命名差异)、状态表达方式、Codex 三种 ack 模式、bot 改名后的查询方法。

## 每轮 poll 流程

每轮分三步:拉数据 → 决策 → 动作。**不要**用单条长 sleep 阻塞会话,由 ScheduleWakeup 控制节奏。

### 步骤 1:拉取当前状态

```bash
bash .agents/skills/pr-ai-review-loop/scripts/poll.sh <PR_NUMBER>
```

JSON 解析后仅保留在对话上下文中,不落盘。

本 skill 维护的三个状态字段及其更新规则:

| 字段 | 更新时机 | 跨 HEAD 行为 | 用途 |
|---|---|---|---|
| `round_count` | HEAD SHA 或 `last_push_at` 与上一轮记录不同时 +1 | 累加,不重置 | 收敛兜底 #1 |
| `topic_history` | 每次拉到 reviewer 新意见时追加一条,记录 `comment id`、`head_sha` 与内容指纹 | 累积不清空,按 `(comment id, head_sha)` + 内容指纹去重(详见下) | 主题指纹比对 |
| `last_commit_shapes` | HEAD SHA 或 `last_push_at` 变化时追加形状标签 | 长度 ≤ 3 的滑窗 | 收敛兜底 #2 |

**`topic_history` 同记录判定**:

- **内容指纹**:walkthrough 取 `updated_at`;其它评论取 `body_head` 的前 N 字符或哈希值
- `(comment id, head_sha)` 命中且**内容指纹未变** → 视为已记录,跳过
- `(comment id, head_sha)` 命中但**内容指纹变了** → 更新已记录条目(覆盖,不重复追加);覆盖 CodeRabbit walkthrough 在同 HEAD 内由 in-progress 改写为 final 的情况
- 跨 HEAD 同 id → 一律视为新一轮

### 步骤 2:对每家启用的 reviewer 决定动作

进入决策表前,先运行 `classify_commits.sh` 判定 commit 性质:

```bash
bash .agents/skills/pr-ai-review-loop/scripts/classify_commits.sh <PR_NUMBER> <previous_round_head_sha>
```

若本轮 push 的所有 commit 均属 fix-up(nit、format、typo、单字段调整、小 bug 修复),本轮**跳过**手动触发 Gemini 与 Codex,等 CodeRabbit 自动跟即可。**例外**:下文 Gemini cold-start fallback 行不受此跳过限制 —— 该场景下 `gemini.reviews` 完全为空,整个 PR 还没经过任何 Gemini review,无论 commit 性质都需补发触发,否则 Gemini 将永远不会审本 PR。

否则按下表执行,命中即执行,同一轮可并行处理多家 reviewer:

| 条件 | 动作 |
|---|---|
| `coderabbit.walkthrough.is_paused == true`,且 `updated_at` 之后未发送过 `@coderabbitai resume`(从 `own_trigger_comments` 中筛,最新一条 `createdAt` 早于 walkthrough 的 `updated_at`,为空时视为未发送) | 发送 `@coderabbitai resume` |
| Gemini 启用,`gemini.reviews` **完全为空**,且 `pr_created_at` 距今不足 5 分钟 | 等待 Gemini 自动 review(PR opened 触发,见 references/reviewers.md);不手动触发 |
| Gemini 启用,`gemini.reviews` **完全为空**,`pr_created_at` 距今已超过 5 分钟,且 `own_trigger_comments` 中不存在 `/gemini review`(或存在但最大 `createdAt ≤ last_push_at`)| 发送 `/gemini review`(cold-start fallback:PR opened 自动 review 未在窗口内出现,可能失败或被跳过;**不受 fix-up 跳过限制**,见上文说明) |
| Gemini 启用,`gemini.reviews` **非空**但最新一条 `submittedAt < last_push_at`,且 `own_trigger_comments` 中不存在 `/gemini review`(或存在但最大 `createdAt ≤ last_push_at`),且**前述跳过触发未命中** | 发送 `/gemini review`(synchronize 场景,Gemini 不自动跟新 commit) |
| Codex 启用,按下方「Codex 触发决策」判断需要触发,且**前述跳过触发未命中** | 发送 `@codex review` |

执行完触发动作后,按下文「轮询节奏」表选择延迟,调用 `ScheduleWakeup`。

**去重原则**:同一 HEAD 上 `/gemini review` 与 `@codex review` 各只能发送一次。每种命令在 `own_trigger_comments` 中取最大 `createdAt`,若晚于 `last_push_at`,视为本轮已触发,跳过。

判定循环走向:

- 仍有 reviewer 未对最新 HEAD 出结果 → 等待下一轮
- 至少一家 reviewer 提交了新的 actionable 意见 → 进入步骤 3
- 所有启用的 reviewer 对当前 HEAD 均已通过 → 退出循环,简短汇报

### 步骤 3:收集意见并转交 receiving-code-review

将所有 reviewer 的本轮新意见**合并为一次调用**,通过 Skill 工具调用 `receiving-code-review`,不要每家单独调用。

合并时必须将 `gemini.reviews[*].body`(summary)整段贴入上下文。Gemini 的某些建议仅出现在 summary 中,inline 部分为空;只贴 inline 会丢失意见。`receiving-code-review` 与本 skill 共享 context,只有把 summary body 摆在对话中它才能读到。

`receiving-code-review` 调用完成后回到步骤 1。该 skill 负责实施修复、回复 reviewer inline、记录 pushback;本 skill 仅重新拉取数据,判断是否产生新 HEAD 或新一轮 review。

## 关键判定

### Reviewer 是否已审查当前 HEAD

- **CodeRabbit**:`coderabbit.walkthrough.updated_at > last_push_at`
- **Gemini**:`gemini.reviews[*].submittedAt > last_push_at` 至少一条
- **Codex**:满足 references/reviewers.md 中 Codex 三种 ack 模式任一

### 是否为 actionable

- **CodeRabbit**:`walkthrough.is_ok == true` 或 `actionable_count == "0"` 时无 actionable;否则查看 `inline_comments_by_user["coderabbitai[bot]"]` 中 `created_at > last_push_at` 的条目,body 开头若含 `_⚠️ Potential issue_`、`_🟠 Major_`、`_🛠️ Refactor suggestion_`、`_💡 Verification agent_` 等标签,均视为 actionable;nit 级别不算
- **Gemini**(两条路径,任一命中即算):
  - **inline 路径**:`inline_comments_by_user["gemini-code-assist[bot]"]` 中 `created_at > last_push_at` 的条目,`severity_alt` 为 `high`、`medium`、`critical` 算 actionable;`low`、`nit`、`style` 不算
  - **summary 路径**:`gemini.reviews` 中 `submittedAt > last_push_at` 的最新一条,body 非空且不含明确通过标记(`LGTM`、`No issues found`、`Approved`、仅有 `## Code Review` 标题而无后续内容),算 actionable
- **Codex**:`inline_comments_by_user["chatgpt-codex-connector[bot]"]` 中 `severity_alt` 为 `Pn Badge` 形式(P0/P1 通常视为 actionable;P2/P3 视情况)

**Acknowledgment 例外**:`inline_comments_by_user.*` 中 `is_ack == true` 的条目,是 reviewer 对上一次修复或 inline 回复的确认响应,**不算** actionable。review state 为 `APPROVED` 一律不算 actionable。

### 是否已通过

当前 HEAD 下,每家启用的 reviewer 满足以下之一:

- **CodeRabbit**:`updated_at > last_push_at` **且** `is_in_progress == false` **且** `is_paused == false`(前置条件:CR 已审过当前 HEAD、不在 in-progress、未 paused;paused 时 `walkthrough` 中 `is_ok` 等字段可能是上一轮残留,需先经步骤 2 中的「CR resume」规则(`@coderabbitai resume`)恢复审查再判通过);在该前置之上,满足任一即通过 —— `walkthrough.is_ok == true` / `actionable_count == "0"` / 本轮 inline 均为 `is_ack == true` / 本轮 inline 均为 nit 级(body 含 `_🧹 Nitpick_` / `_🔵 Trivial_` / `_💤 Low value_` 标签,不含 `_⚠️ Potential issue_` / `_🟠 Major_` / `_🛠️ Refactor suggestion_` / `_💡 Verification agent_`)
- **Gemini**:`gemini.reviews[*].submittedAt > last_push_at` 至少一条(前置条件:Gemini 已审过当前 HEAD,避免误用上一轮的通过标记);在该前置之上,需**同时**满足:(1) 本轮无新 inline 或本轮新 inline 全部为 `low/nit/style` 或全部为 `is_ack`;(2) summary 最新一条 `gemini.reviews` 的 body 含明确通过标记(非空不等于通过)
- **Codex**:满足 references/reviewers.md 中三种 ack 模式之一,且本轮无 ack 以外的 inline
- 或该 reviewer 已被用户临时停用

### Codex 触发决策

Codex 是否自动跟新 commit 取决于仓库配置(详见 references/reviewers.md)。仓库未开启自动 review 时,是否手动 `@codex review` 综合判断:

- 用户明确意图(提到 codex 通常意味着需要触发)
- CodeRabbit 与 Gemini 意见冲突,需要第三方仲裁
- PR 改动面值得多看一遍(敏感模块、跨模块影响、新增依赖等)
- 当前 HEAD 是否已触发过(去重)
- 跳过触发是否命中(本轮全为 fix-up 则跳过)

### 轮询节奏

每轮 poll 与决策完成后,调用 `ScheduleWakeup` 安排下一次唤醒。延迟取值:

| 场景 | 延迟 | 备注 |
|---|---|---|
| 新 HEAD 后首次 poll | 180s | reviewer cold-start;CR 通常 60-90s 跟新 HEAD;Gemini 仅 PR opened 自动 review,synchronize 不跟;Codex 取决于仓库配置 |
| 发送 `/gemini review` 或 `@codex review` 之后 | 120s | Gemini 响应通常 90-120s,60s 容易错过 |
| 常规等待(reviewer 响应中) | 60s | 处于 prompt cache 5 分钟窗口内 |
| 超过 15 分钟无响应 | 暂停并询问用户,不再 ScheduleWakeup | 与故障类暂停一致 |

## 收敛兜底

下列任一条件触发退出:

1. `round_count >= 8` → 暂停询问"已 8 轮,merge / 继续 / 放弃?"
2. 连续 2 轮 `last_commit_shapes` 全为 nit / format → 暂停询问"边际收益已降低,是否结束?"
3. 同一主题指纹连续提出 ≥ 3 轮(与「运行模式」B 节联动)→ 暂停询问是否升级 ADR
4. 所有 reviewer 对当前 HEAD 均通过 → 正常退出

## 故障处理

- **某家 reviewer 长时间无响应**:bot 可能服务异常或配额已满。超过 15 分钟暂停并询问用户(与轮询节奏上限一致)
- **bot 报错**(如 "Internal error"、"Token limit exceeded"):将错误内容贴给用户,询问是否强制重跑(`@coderabbitai full review` 或 `/gemini review`)
- **`poll.quota_alerts` 非空**:bot 在 PR 中留下了 quota / rate limit 报错。将 `body_head` 贴给用户,询问是否暂时停用该 reviewer 继续其他家,或等 quota 恢复后再 push
- **`gh` 401/403**:请用户运行 `gh auth refresh -s repo`
- **脚本报 `POLL_ERROR:`**:重试一次(网络抖动常见),再失败则将 stderr 贴给用户
- **CI 失败**:CodeRabbit 会等 GitHub Checks 完成后继续;CI 红时 review 可能不会触发,优先修复 CI

## 与其他 skill 的分工

| 任务 | 对应 skill |
|---|---|
| 创建 PR | `commit-commands:commit-push-pr` |
| 回应 / 实施 / 反驳 review 意见 | `receiving-code-review` |
| 验证修复是否真的解决问题 | `verify` |
| **监控多家 AI reviewer 的循环节奏** | **本 skill** |

本 skill 仅负责调度:何时 poll、何时 resume 或触发、何时转交 `receiving-code-review`、何时结束循环。**不**负责"回应意见"与"验证修复"。
