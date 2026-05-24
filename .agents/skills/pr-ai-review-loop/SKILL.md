---
name: pr-ai-review-loop
description: PR 提交后无人值守驱动 AI reviewer（CodeRabbit、Gemini Code Assist、OpenAI Codex）的 review → 修复 → push → 再 review 循环。在以下场景主动调用：用户刚跑完 `/commit-push-pr` 或刚 push 了 PR；用户提到"盯着 coderabbit / 监控 PR review / 等审查完 / 处理 coderabbit / gemini review / codex review / watch the PR / wait for AI review"；CodeRabbit 被 pause 后需要重新唤醒；任一 AI reviewer 出现 actionable comments 需要处理。
---

# AI Review Auto-Loop

PR 提交后,多家 AI reviewer 的 review → 修复 → push → 再 review 循环交给本 skill 调度:盯状态、必要时手动唤起、把意见汇总后交给 `receiving-code-review` 处理。

## 运行模式:无人值守 + 三类调度停问

skill 的设计 intent 是**自动跑完整个循环**,不要每轮停下来征求用户授权。按决策表自决:触发命令、push 修复、inline reply、下一轮 poll 全部自决推进。

**驱动方式(重要):** skill 自带 self-pace —— 每轮 poll 决策完成后,用 `ScheduleWakeup` 工具排下次唤醒,harness 在指定延迟后自动回调本 skill。**不需要**外层 `/loop` 模式包裹,也**不需要**用户手动催 "继续"。`delaySeconds` 见 §「polling 节奏」表;`prompt` 字段按当前上下文(PR 号、轮数)自行组织,能让 harness 重新启动本 skill 即可。

**停问触发分两类:**

**A. 故障停问**:
- bot 报错("Internal error" / "Token limit exceeded" 等)
- 某个 reviewer 超过 15 分钟无响应
- `gh` 401/403 认证失败
- `poll.sh` / `classify_commits.sh` 失败重试一次后仍报错
- receiving-code-review 内部判定需要 pushback 但语义不清的 review 意见

**B. 调度停问**:
- **未决策的 fundamental** —— 同一**主题指纹**(reviewer + 关键词,如 "Pydantic `extra=ignore` vs `forbid`")连续 ≥ 2 轮被同家 reviewer 提且无 ADR / memory 兜底 → 停问"是否升级到 ADR PR / 由用户拍板"
- **reviewer 间冲突** —— 同议题 A 家说 X、B 家说 not X → 停问用户裁判,不自决站队
- **业务策略 trade-off** —— 修复方案在前向兼容 / 性能 / 用户体验上有显著差异,违背用户业务意图的风险 → 停问

> 调度停问 ≠ 违背无人值守。无人值守指**可自决的循环动作**(poll/触发/合并意见/push)继续自决;只有**超出 skill 调度边界的根本性争议**升级到用户。

主题指纹靠 Claude 在 context 中维护 `topic_history`(每轮把"reviewer + 一句话主题摘要"附加),靠语义相似性判定同主题。不脚本化、不持久化。

其它一切(cold-start 等待、触发 `/gemini review`、判 acknowledgment vs actionable、是否叫 Codex、新 HEAD 后回 poll、commit / push 节奏)都自决。

## 前置条件

- 分支已有对应 PR(`gh pr view` 能拿到 PR 号);没有就停下来建议先跑 `/commit-commands:commit-push-pr`
- `gh` 已登录且能评论(`gh auth status` 通过)
- 仓库已接入 CodeRabbit、Gemini Code Assist、OpenAI Codex 三家 reviewer
- `jq` 在 PATH 上(macOS / Linux 默认有 / 用 brew install jq;Windows 走 WSL)

## 三家 reviewer 速查

详见 [references/reviewers.md](references/reviewers.md) —— bot 名(GraphQL vs REST)、状态表达方式、Codex 三种 ack 模式、bot 改名查询。

## 每轮 poll 的步骤

每轮做一次"拉数据 → 决策 → 动作"。**不要**用单条长 sleep 把会话卡死。

### 1. 拉当前状态

```bash
bash .agents/skills/pr-ai-review-loop/scripts/poll.sh <PR_NUMBER>
```

输出单一 JSON。字段 schema、设计意图、关键踩坑(为什么 `created_at > last_push_at` 而非 `commit_id == head` 等)全部写在 `scripts/poll.sh` 的头部注释里——第一次进循环时 Read 一遍脚本注释,之后只看 JSON 输出。

把 JSON 解析结果**记在对话上下文里**,不要落盘。同时更新:
- `round_count` (+1)
- `topic_history` (从本轮 reviewer 意见提炼新主题摘要)
- `last_commit_shapes` (见下方「收敛兜底」)

### 2. 对每个启用的 reviewer 决定动作

**保守触发前置:** 在执行下方决策表前,先用 `classify_commits.sh` 看本轮 push 的 commit 性质:

```bash
bash .agents/skills/pr-ai-review-loop/scripts/classify_commits.sh <PR_NUMBER> <previous_round_head_sha>
```

输出每条 commit 的 `{files_changed, lines_added, message_head, ...}`。若本轮 commit 集合**全部**是 fix-up(nit / format / typo / 单字段修改 / 小 bug),Claude 主观判定后 → **跳过手动 `/gemini review` / `@codex review` 触发**,等 CodeRabbit 自动跟即可(CR 自动跟新 commit,无 quota 顾虑)。理由:Gemini / Codex 都是全量 PR 重审,quota 稀缺资源。

否则按下表问一遍,命中即执行;同一轮可以并行处理多个 reviewer:

| 当前状态 | 动作 |
|---|---|
| `coderabbit.walkthrough.is_paused == true`,且其 `updated_at` 之后未发过 `@coderabbitai resume`(从 `own_trigger_comments` 里过滤,看最新一条 `createdAt` 是否早于 walkthrough `updated_at`,空则按"未发过"处理) | 发 `@coderabbitai resume` |
| Gemini 启用,本轮 push 后 `gemini.reviews` 中无 `submittedAt > last_push_at` 的条目,且 `own_trigger_comments` 中 `/gemini review` 的最大 `createdAt ≤ last_push_at` —— 且**未触发保守跳过** | 发 `/gemini review` |
| Codex 启用且按 §「Codex 触发决策」判断认为该叫 —— 且**未触发保守跳过** | 发 `@codex review` |
| 还有 reviewer 在最新 HEAD 上没出结果 | 等下一轮(见 §「polling 节奏」) |
| 至少一个 reviewer 给出新 actionable 意见 | 进步骤 3 |
| 所有启用的 reviewer 都对当前 HEAD 给绿灯(见 §「怎么算已通过」) | 退出并简短汇报 |

**去重原则:** 同一 HEAD 上 `/gemini review` 和 `@codex review` 各只能发一次。对每个命令类型在 `own_trigger_comments` 里取 `max(createdAt)`,若 `> last_push_at` 则视为本轮已触发,跳过。

### 3. 收意见 → 交给 receiving-code-review

把所有 reviewer 的新意见**合并一次**通过 Skill 工具调用 `receiving-code-review`,不要逐家分调。**重要:** 合并时把 `gemini.reviews[*].body`(summary)全文一并列出,不要只列 inline items —— Gemini 经常把唯一建议放在 summary 里,inline 0 条。receiving-code-review 与本 skill 共享 context,把 summary body 摆到对话里它才能看到。

receiving-code-review 返回后回步骤 1。它自己负责实施修复、向 reviewer 写回复、记录 pushback——本 skill 只重新拉数据看是否产生了新 HEAD 或新一轮 review。

## 关键判断

### 怎么判 "Reviewer 已审过当前 HEAD"

- **CodeRabbit**: `coderabbit.walkthrough.updated_at > last_push_at`
- **Gemini**: `gemini.reviews[*].submittedAt > last_push_at` 至少一条
- **Codex**: 满足 references/reviewers.md 的 Codex 三种 ack 模式任一

### 怎么算 "actionable"

- **CodeRabbit** → `coderabbit.walkthrough.is_ok == true` 或 `actionable_count == "0"` 时**无** actionable;否则看 `inline_comments_by_user["coderabbitai[bot]"]` 中 `created_at > last_push_at` 的条目 body 开头是否带 `_⚠️ Potential issue_` / `_🟠 Major_` / `_🛠️ Refactor suggestion_` / `_💡 Verification agent_` 等标签——非 nit 级别都算 actionable
- **Gemini** → **双路径任一命中**:
  - **inline 路径**: `inline_comments_by_user["gemini-code-assist[bot]"]` 中 `created_at > last_push_at` 的 items `severity_alt` 含 `high` / `medium` / `critical` 算 actionable;`low` / `nit` / `style` 不算
  - **summary 路径**: `gemini.reviews` 中 `submittedAt > last_push_at` 的最新条目 body **非空** 且 **不**含明确通过 marker(`LGTM` / `No issues found` / `Approved` / 单一 `## Code Review` 标题无后文)→ 算 actionable
- **Codex** → `inline_comments_by_user["chatgpt-codex-connector[bot]"]` 中 `severity_alt` 是 `Pn Badge` 形式(P0/P1 通常算 actionable,P2/P3 视场景)

**Acknowledgment 例外:** `inline_comments_by_user.*` 中 `is_ack == true` 的条目是 reviewer 对前次 fix / inline reply 的**确认回复**,**不计入** actionable。
review state == `APPROVED` 一律算无 actionable。

### 怎么算 "已通过"

当前 HEAD 下,每个启用的 reviewer 满足以下之一:

- **CodeRabbit**: `walkthrough.is_ok == true`(或 `actionable_count == "0"`),**或**本轮 inline 全是 `is_ack == true`,且 `updated_at > last_push_at`,且 `is_in_progress == false`(in-progress 时先回 poll)
- **Gemini**:
  - inline 部分本轮(`created_at > last_push_at`)全 `low/nit/style` 或全 `is_ack`,**且**
  - summary 部分最新 `gemini.reviews` 条目 body 含明确通过 marker(非空 ≠ 通过)
- **Codex**: 满足 references/reviewers.md 中三种 ack 模式之一,且本轮无非 ack inline
- 或该 reviewer 被用户临时禁用

### Codex 触发决策

Codex 是否跟新 commit 按仓库配置(详见 references/reviewers.md)。若仓库未开自动,是否手动 `@codex review` 按必要性自行判断:

- 用户的明确意图(提到 codex 就基本是要叫)
- CodeRabbit 与 Gemini 的意见是否存在重大分歧,需要第三方仲裁
- PR 改动面是否值得多一份独立审查(敏感面、跨模块影响、新增依赖等)
- 是否已经在本 HEAD 上叫过(去重)
- **是否被保守触发前置跳过**(本轮全 fix-up)

### polling 节奏

每轮 poll 决策完调 `ScheduleWakeup` 排下次唤醒(见 §「运行模式」驱动方式)。`delaySeconds` 按下表:

| 场景 | delay | 备注 |
|---|---|---|
| 新 HEAD 后第一次 poll | **180s** | reviewer cold-start;CR 通常 60-90s 跟新 HEAD,Gemini 不自动跟,Codex 看仓库配置 |
| 触发 `/gemini review` / `@codex review` 后 | **120s** | Gemini 响应通常 90-120s,60s 易扑空 |
| 常规 poll(等待 reviewer 响应) | **60s** | 命中 cache 窗口(5min) |
| 超 15 分钟无动静 | **停下来问用户**,不再 ScheduleWakeup | 与故障停问一致 |

所有 wakeup 间隔(60/120/180s)都在 prompt cache 5min 窗口内,context 缓存不会失效。只有故障停问跨窗口。

## 收敛兜底

退出触发**任一即停**:

1. **`round_count >= 8`** → 停问"已 8 轮,是否就此 merge / 继续 / 放弃"
2. **连续 2 轮 `last_commit_shapes` 全是 nit/format**(用 `classify_commits.sh` 输出 + Claude 主观判) → 停问"边际收益递减,是否就此结束"
3. **同一 `topic_history` 主题指纹连续 ≥ 3 轮被提**(与 §运行模式 B 类联动) → 停问升级到 ADR
4. **所有 reviewer 对当前 HEAD 绿灯** → 正常退出

上下文跟踪 `round_count` / `last_commit_shapes`(长度 ≤ 3 序列)/ `topic_history` 全部 in-context,不持久化。

## 故障处理

- **某个 reviewer 一直不回**:bot 可能挂了 / 配额满。15 分钟没动静就停下来问用户(与 polling 节奏上限一致)
- **bot 报错**("Internal error" / "Token limit exceeded"):把错误内容贴给用户,问要不要发 `@coderabbitai full review` / `/gemini review` 强制重跑
- **`poll.quota_alerts` 非空**:bot 在 PR 留了 quota / rate limit 报错——把 `body_head` 贴给用户,问要否暂时禁用该 reviewer 继续别家,或等 quota 恢复后再 push
- **`gh` 401/403**:让用户跑 `gh auth refresh -s repo`
- **`poll.sh` / `classify_commits.sh` 报 `POLL_ERROR:`**:重试一次(网络抖动常见),再失败把 stderr 贴给用户
- **CI 失败**:CodeRabbit 会等 GitHub Checks 跑完再继续;CI 红时 review 可能不来——先帮用户修 CI

## 与其他 skill 的边界

| 任务 | 用哪个 |
|---|---|
| 创建 PR | `commit-commands:commit-push-pr` |
| 回应 / 实施 / 反驳 review 意见 | `receiving-code-review` |
| 验证修复是否真的解决问题 | `verify` |
| **盯多 AI reviewer 的循环节奏** | **本 skill** |

本 skill 只做调度——什么时候 poll、什么时候 resume/触发、什么时候把控制权交给 receiving-code-review、什么时候结束循环。**不**负责"如何回应意见"和"如何验证修复"。
