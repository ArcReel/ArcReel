---
name: pr-ai-review-loop
description: 无人值守驱动 CodeRabbit、Gemini Code Assist、OpenAI Codex 的 review → 修复 → push → 再 review 循环,直到全部通过或触发收敛退出。主动调用:用户刚 push PR 或跑完 /commit-push-pr;提到 review / coderabbit / gemini / codex / 审查 / AI review / 等 bot 回复;CodeRabbit paused 需 resume;reviewer 有 actionable comments。即使用户只说"PR 怎么样了""review 回了吗"也应触发。
---

# AI Review Auto-Loop

PR push 上去之后,CodeRabbit / Gemini / Codex 三家 AI reviewer 反复 review → 触发修复 → push → 再 review。本 skill 负责调度:盯状态、必要时手动催、把意见收拢后交给 `receiving-code-review` 处理。

## 运行模式:无人值守 + 两类停问

自动跑完整个循环,不要每轮停下来征求授权。该不该触发命令、要不要 push 修复、回不回 inline、什么时候下一轮 poll,按决策表自行决定。

**驱动方式(重要):** 本 skill 自带 self-pace。每轮 poll + 决策做完后,直接调 `ScheduleWakeup` 排下一次唤醒,到点 harness 会自动重新进入本 skill。**不用**外层套 `/loop`,也**不用**等用户输入"继续"。`delaySeconds` 见下文「polling 节奏」表;`prompt` 字段按当前 PR 号和轮数自行组织,能让 harness 重新拉起本 skill 即可。

### A. 故障停问

- bot 报错("Internal error" / "Token limit exceeded" 这类)
- 某家 reviewer 15 分钟以上没回
- `gh` 401/403 认证失败
- `poll.sh` / `classify_commits.sh` 重试一次还报错
- review 意见语义模糊,receiving-code-review 觉得要 pushback 但拿不准

### B. 调度停问

- **根本性分歧没定论** —— 同一个**主题指纹**(reviewer + 关键词,比如 "Pydantic `extra=ignore` vs `forbid`")同一家 reviewer 连提 ≥ 2 轮,而且没有 ADR / memory 兜底 → 停下来请用户裁决是否升级 ADR
- **reviewer 之间打架** —— 同一件事 A 家说 X、B 家说不该 X → 停下来交给用户裁决,不自行选边
- **业务取舍** —— 修复方案在前向兼容 / 性能 / 用户体验上有明显差别,可能踩到业务意图 → 停下来确认

> 调度停问不等于违背无人值守。无人值守指**循环里能自行决定的动作**(poll / 触发 / 合并意见 / push)继续自行决定;只有**超出本 skill 调度范围的根本性争议**才升级到用户。

主题指纹由 Claude 在对话上下文里维护 `topic_history`(每轮追加"reviewer + 一句话主题摘要"),靠语义相似度判同主题。不脚本化、不落盘。

其它一切(cold-start 等多久、要不要发 `/gemini review`、ack 还是 actionable、要不要叫 Codex、新 HEAD 后什么时候回来 poll、commit / push 节奏)都自行决定。

## 前置条件

- 分支上已经有对应 PR(`gh pr view` 能拿到 PR 号);没有就停下来,建议先跑 `/commit-commands:commit-push-pr`
- `gh` 登录正常且有评论权限(`gh auth status` 通过)
- 仓库已经接入 CodeRabbit、Gemini Code Assist、OpenAI Codex 三家 reviewer
- `jq` 在 PATH 上(macOS / Linux 默认有,没有就 brew install jq;Windows 走 WSL)

## 三家 reviewer 速查

看 [references/reviewers.md](references/reviewers.md) —— bot 名(GraphQL vs REST)、状态表达方式、Codex 三种 ack 模式、bot 改名怎么查。

## 每轮 poll 的步骤

每轮流程:拉数据 → 决策 → 动作。**不要**用单条长 sleep 把会话卡死。

### 1. 拉当前状态

```bash
bash .agents/skills/pr-ai-review-loop/scripts/poll.sh <PR_NUMBER>
```

脚本输出一个 JSON。字段 schema、设计意图、关键踩坑(比如为什么用 `created_at > last_push_at` 而不是 `commit_id == head`)都写在 `scripts/poll.sh` 头部注释里 —— 第一次进循环时 Read 一遍脚本注释,之后只看 JSON 输出。

JSON 解析后**只放在对话上下文里**,不要落盘。下面三个状态字段的更新触发条件**各不相同**,分别描述:
- `round_count` —— **只在本轮 `last_push_at` / HEAD SHA 与上一轮记录的不同时 +1**(初次进入算第 1 轮);HEAD 没变、仅仅是 wakeup 回来继续等 reviewer 出意见的 poll **不计**(一轮 = 一次"修复 → push → reviewer 回意见"周期,不是"poll 了多少次")
- `topic_history` —— **每次 poll 拉到 reviewer 新意见时都追加**(不绑定 HEAD 切换);entry 形式建议 `{reviewer, round_count, 主题摘要}` —— reviewer 用于 line 26 的"同一家连提"判定,round_count 用于 line 152 收敛兜底 #3 的"≥ 3 轮"计数。**跨 HEAD 累积、不清空**:收敛兜底 #3 的"同一主题指纹连提 ≥ 3 轮"靠它跨 HEAD 比对(line 32 的语义相似度判同主题),清空就破坏了这个判定。同 HEAD 内多次 poll 拉到的同一条 reviewer comment 只入一次,避免噪声;初次进入或主题未出现过时直接追加,不用做相似度比对
- `last_commit_shapes` —— **只在本轮 `last_push_at` / HEAD SHA 与上一轮记录的不同时追加**一条 Claude 看 `classify_commits.sh` 输出后概括的简短标签(如 `all_nit/format` / `contains_functional`),供收敛兜底 #2 判定;长度 ≤ 3 的滑窗

### 2. 对每家启用的 reviewer 决定动作

**先做保守触发前置:** 看决策表之前,先跑 `classify_commits.sh` 看本轮 push 的 commit 性质:

```bash
bash .agents/skills/pr-ai-review-loop/scripts/classify_commits.sh <PR_NUMBER> <previous_round_head_sha>
```

每条 commit 输出 `{files_changed, lines_added, message_head, ...}`。如果本轮 push 的 commit **全部**是 fix-up(nit / format / typo / 改一个字段 / 小 bug),Claude 自行判断 → **本轮跳过手动 `/gemini review` / `@codex review` 触发**,等 CodeRabbit 自动跟即可(CR 跟新 commit 是自动的,不消耗 quota)。理由:Gemini / Codex 每次都是整个 PR 全审,quota 是稀缺资源。

否则按下表过一遍,命中就执行;同一轮可以并行处理多家 reviewer:

| 当前状态 | 动作 |
|---|---|
| `coderabbit.walkthrough.is_paused == true`,且它 `updated_at` 之后还没发过 `@coderabbitai resume`(从 `own_trigger_comments` 里筛,看最新一条 `createdAt` 是不是早于 walkthrough 的 `updated_at`,空就当"没发过") | 发 `@coderabbitai resume` |
| Gemini 启用,本轮 push 之后 `gemini.reviews` 里没有 `submittedAt > last_push_at` 的条目,且 `own_trigger_comments` 中 `/gemini review` 的最大 `createdAt ≤ last_push_at` —— 而且**上面保守跳过没命中** | 发 `/gemini review` |
| Codex 启用,按下方「Codex 触发决策」判断该叫 —— 而且**上面保守跳过没命中** | 发 `@codex review` |
| 还有 reviewer 没在最新 HEAD 上出结果 | 等下一轮(见下文「polling 节奏」) |
| 至少一家 reviewer 给了新的 actionable 意见 | 进步骤 3 |
| 所有启用的 reviewer 都对当前 HEAD 亮绿灯(见下文「怎么算已通过」) | 退出,简短汇报 |

**去重原则:** 同一 HEAD 上 `/gemini review` 和 `@codex review` 各只能发一次。每种命令在 `own_trigger_comments` 里取最大 `createdAt`,只要比 `last_push_at` 新,就当本轮已经触发过,跳过。

### 3. 收意见 → 交给 receiving-code-review

把所有 reviewer 的新意见**合到一起一次性**通过 Skill 工具调 `receiving-code-review`,不要每家单独调一次。**重点:** 合的时候把 `gemini.reviews[*].body`(summary)整段贴出来,不要只贴 inline items —— Gemini 经常唯一一条建议就藏在 summary 里,inline 一条没有。receiving-code-review 跟本 skill 共享 context,只有把 summary body 摆到对话里它才看得到。

receiving-code-review 调完回步骤 1。它自己负责动手修、给 reviewer 回 inline、记 pushback —— 本 skill 只重新拉数据,看有没有新 HEAD 或新一轮 review。

## 关键判断

### 怎么判"Reviewer 审过当前 HEAD 了"

- **CodeRabbit**:`coderabbit.walkthrough.updated_at > last_push_at`
- **Gemini**:`gemini.reviews[*].submittedAt > last_push_at` 至少一条
- **Codex**:满足 references/reviewers.md 里 Codex 三种 ack 模式任一

### 怎么算"actionable"

- **CodeRabbit** → `coderabbit.walkthrough.is_ok == true` 或 `actionable_count == "0"` 就**没有** actionable;否则看 `inline_comments_by_user["coderabbitai[bot]"]` 里 `created_at > last_push_at` 的条目,body 开头有没有 `_⚠️ Potential issue_` / `_🟠 Major_` / `_🛠️ Refactor suggestion_` / `_💡 Verification agent_` 这类标签 —— 只要不是 nit 级别都算 actionable
- **Gemini** → **两条路径,任一命中即算**:
  - **inline 路径**:`inline_comments_by_user["gemini-code-assist[bot]"]` 里 `created_at > last_push_at` 的 items,`severity_alt` 是 `high` / `medium` / `critical` 算 actionable;`low` / `nit` / `style` 不算
  - **summary 路径**:`gemini.reviews` 里 `submittedAt > last_push_at` 的最新一条,body **不为空** 而且 **不含**明确的通过标记(`LGTM` / `No issues found` / `Approved` / 只有一个 `## Code Review` 标题后面没内容)→ 算 actionable
- **Codex** → `inline_comments_by_user["chatgpt-codex-connector[bot]"]` 里 `severity_alt` 是 `Pn Badge` 形式(P0/P1 一般算 actionable,P2/P3 看情况)

**Acknowledgment 例外:** `inline_comments_by_user.*` 里 `is_ack == true` 的条目是 reviewer 对上一次修复 / inline 回复的**确认回应**,**不算** actionable。
review state == `APPROVED` 一律不算 actionable。

### 怎么算"已通过"

当前 HEAD 下,每家启用的 reviewer 满足以下之一:

- **CodeRabbit**:`walkthrough.is_ok == true`(或 `actionable_count == "0"`),**或**本轮 inline 全是 `is_ack == true`,而且 `updated_at > last_push_at`,而且 `is_in_progress == false`(还在 in-progress 就先回 poll)
- **Gemini**:
  - inline 部分本轮(`created_at > last_push_at`)全是 `low/nit/style` 或全是 `is_ack`,**而且**
  - summary 部分最新一条 `gemini.reviews` 的 body 含明确通过标记(非空不等于通过)
- **Codex**:满足 references/reviewers.md 三种 ack 模式之一,而且本轮没有 ack 以外的 inline
- 或该 reviewer 被用户临时停用

### Codex 触发决策

Codex 跟不跟新 commit 看仓库配置(详见 references/reviewers.md)。仓库未开自动 review 时,是否手动 `@codex review` 自行判断:

- 用户的明确意图(提到 codex 基本就是要触发)
- CodeRabbit 和 Gemini 意见冲突,是否需要第三方仲裁
- PR 改动面是否值得多看一遍(敏感模块、跨模块影响、新加依赖这类)
- 本 HEAD 上是否已经触发过(去重)
- **保守跳过是否命中**(本轮全是 fix-up)

### polling 节奏

每轮 poll + 决策做完就调 `ScheduleWakeup` 排下一次唤醒(见上文「驱动方式」)。`delaySeconds` 按下表:

| 场景 | delay | 备注 |
|---|---|---|
| 新 HEAD 后第一次 poll | **180s** | reviewer cold-start;CR 一般 60-90s 跟新 HEAD,Gemini 不自动跟,Codex 看仓库配置 |
| 发完 `/gemini review` / `@codex review` 之后 | **120s** | Gemini 响应通常 90-120s,60s 容易扑空 |
| 常规 poll(等 reviewer 响应) | **60s** | 在 cache 窗口里(5min) |
| 超过 15 分钟还没动静 | **停下来问用户**,不再 ScheduleWakeup | 跟故障停问一致 |

所有 wakeup 间隔(60/120/180s)都在 prompt cache 5min 窗口里,context 缓存不会失效。只有故障停问会跨窗口。

## 收敛兜底

下面任一条触发就退出:

1. **`round_count >= 8`** → 停下来问"已经 8 轮了,merge / 继续 / 放弃?"
2. **连续 2 轮 `last_commit_shapes` 全是 nit/format**(`classify_commits.sh` 输出 + Claude 自行判断) → 停下来问"再改下去边际收益小了,是否结束?"
3. **同一个 `topic_history` 主题指纹连提 ≥ 3 轮**(和上文「运行模式 B」联动) → 停下来询问是否升级 ADR
4. **所有 reviewer 对当前 HEAD 都亮绿灯** → 正常退出

`round_count` / `last_commit_shapes`(长度 ≤ 3 的滑窗)/ `topic_history` 都在对话上下文里维护,不落盘。

## 故障处理

- **某家 reviewer 一直不回**:bot 可能服务异常或配额已满。15 分钟没动静就停下来问用户(跟 polling 节奏的上限一致)
- **bot 报错**("Internal error" / "Token limit exceeded"):把错误内容贴给用户,问要不要发 `@coderabbitai full review` / `/gemini review` 强制重跑
- **`poll.quota_alerts` 不为空**:bot 在 PR 里留了 quota / rate limit 报错 —— 把 `body_head` 贴给用户,问要不要暂时停用该 reviewer 继续其他家,或等 quota 恢复再 push
- **`gh` 401/403**:让用户跑 `gh auth refresh -s repo`
- **`poll.sh` / `classify_commits.sh` 报 `POLL_ERROR:`**:重试一次(网络抖动常见),再失败就把 stderr 贴给用户
- **CI 失败**:CodeRabbit 会等 GitHub Checks 跑完再继续;CI 红时 review 可能不会触发 —— 先帮用户修 CI

## 与其他 skill 的分工

| 任务 | 用哪个 |
|---|---|
| 创建 PR | `commit-commands:commit-push-pr` |
| 回应 / 实施 / 反驳 review 意见 | `receiving-code-review` |
| 验证修复是否真的解决问题 | `verify` |
| **盯多家 AI reviewer 的循环节奏** | **本 skill** |

本 skill 只负责调度 —— 什么时候 poll、什么时候 resume / 触发、什么时候把活交给 receiving-code-review、什么时候结束循环。**不**负责"回应意见"和"验证修复"。
