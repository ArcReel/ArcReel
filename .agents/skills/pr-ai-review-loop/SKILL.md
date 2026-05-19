---
name: pr-ai-review-loop
description: PR 提交后自动盯 AI reviewer（CodeRabbit、Gemini Code Assist、OpenAI Codex）的循环驱动 skill。三家表达状态的方式各不相同——CodeRabbit 反复编辑首条评论（walkthrough）；Gemini 每次都发新评论，严重度标签在 inline review 上；Codex **每次审查二选一**：有建议→发评论 / 无建议→给 PR 加 👍 reaction。CodeRabbit 自动跟新 commit；Gemini / Codex 不自动跟，需要 push 后手动触发（`/gemini review`、`@codex review`），其中 Codex 由 Claude 按必要性自行判断。请在以下场景**主动**调用：用户刚跑完 `/commit-push-pr` 或刚 push 了 PR；用户提到 "盯着 coderabbit / 监控 PR review / 等审查完 / 处理 coderabbit / gemini review / codex review / watch the PR / wait for AI review"；CodeRabbit 被 pause 后需要重新唤醒；任一 AI reviewer 出现 actionable comments 需要处理。skill 会识别 pause 状态并发 `@coderabbitai resume`，并在新 HEAD 上唤起 Gemini。
---

# AI Review Auto-Loop

PR 提交后，多家 AI reviewer 的 review → 修复 → push → 再 review 循环交给本 skill 调度：盯状态、必要时手动唤起、把意见汇总后交给 `superpowers:receiving-code-review` 处理。

## 运行模式：无人值守

skill 的设计 intent 是**自动跑完整个循环**，不要每轮停下来征求用户授权。按决策表自决：触发命令、push 修复、inline reply、下一轮 poll 全部自决推进。只在以下情形停下来问用户：

- bot 报错（"Internal error" / "Token limit exceeded" 等）
- 某个 reviewer 超过 15 分钟无响应
- gh 401/403 认证失败
- receiving-code-review 内部判定需要 pushback 但语义不清的 review 意见（这是 receiving-code-review 自己的 ask）

其它一切（cold-start 等待、触发 `/gemini review`、判 acknowledgment vs actionable、是否叫 Codex、新 HEAD 后回 poll、commit / push 节奏）都自决。

## 前置条件

- 分支已有对应 PR（`gh pr view` 能拿到 PR 号）；没有就停下来建议先跑 `/commit-commands:commit-push-pr`
- `gh` 已登录且能评论（`gh auth status` 通过）
- 仓库已接入 CodeRabbit、Gemini Code Assist、OpenAI Codex 三家 reviewer

## 三家 AI Reviewer 速查

**三家表达状态的方式不一样，必须用对应方法读，否则会漏判 / 误判。**

| Reviewer | GraphQL `author.login` | 自动跟新 commit | 状态表达方式 | 触发命令 |
|---|---|---|---|---|
| CodeRabbit | `coderabbitai` | **是** | **反复编辑首条评论（walkthrough）**：`updatedAt` 会被推后，body 开头有 `<!-- ... summarize by coderabbit.ai -->` HTML 注释。OK 时 body 首行：`No actionable comments were generated in the recent review. 🎉`。其余 reply 是另算的会话评论 | `@coderabbitai resume` / `review` / `full review` |
| Gemini Code Assist | `gemini-code-assist` | 否 | **review summary** 每次发新评论（body 以 `## Code Review` 开头，是 PR 总结，不含 severity）；**严重度标签在 inline review comments**：body 开头是 `![high](https://www.gstatic.com/codereviewagent/high-priority.svg)` 这种 markdown image | `/gemini review` |
| OpenAI Codex | `chatgpt-codex-connector` | **按仓库配置**：默认要手动 `@codex review`；某些仓库（如 ArcReel/ArcReel）开了 PR 自动 review，Codex 会自动跟新 commit。第 0 轮 poll 实测：push 后几分钟看 `codex_reviews` 是否自然出现新条目即知 | **三种状态信号**：① 有建议→发 review comment（body 开头 `### 💡 Codex Review`，含 `**Reviewed commit:** <SHA>`）；② 无建议（无 cross-check）→给 PR 加 👍 reaction（不留评论）；③ **空 body review** (`state=COMMENTED, body=""`)+ 无新 inline+ 无 reaction—— 也视为 ack（实测：Codex 在新 HEAD 自动跟时若无新意见，可能用这条空 review 代替 reaction） | `@codex review` |

**其它 bot**（如 `github-code-quality[bot]` GitHub 自带静态分析、`codecov[bot]` 覆盖率）默认**不**纳入主循环决策——它们的输出通常是死板的 nit / 数字，没有"等待"或"重审"概念。它们的 inline 意见在调用 `receiving-code-review` 时被一并看到。

用户可随时让某个 reviewer 进 / 出循环（"这次别管 gemini"、"叫上 codex"、"也看看 code-quality"），按上下文意图执行。

### REST vs GraphQL 命名陷阱

| 数据源 | 字段路径 | 是否带 `[bot]` |
|---|---|---|
| `gh pr view --json reviews,comments,...` (GraphQL) | `.author.login` | **不带** —— 如 `coderabbitai` |
| `gh api repos/.../pulls/.../comments` (REST inline) | `.user.login` | **带** —— 如 `coderabbitai[bot]` |
| `gh api repos/.../issues/.../reactions` (REST) | `.user.login` | **带** —— 如 `chatgpt-codex-connector[bot]` |

混用必踩坑。**两个端的字符串不通用**，匹配前先确认数据来源。

bot 改名时跑这条拿最新 GraphQL 名：

```bash
gh pr view <PR> --json reviews,comments \
  --jq '[.reviews[].author.login, .comments[].author.login] | unique'
```

## 每轮 poll 的步骤

每轮做一次"拉数据 → 决策 → 动作"。**不要**用单条长 sleep 把会话卡死。

### 1. 拉当前状态

**主查询**（reviews + comments + 自己发过的触发命令）：

```bash
gh pr view <PR_NUMBER> --json number,headRefOid,reviews,comments,commits \
  --jq '{
    pr: .number,
    head: .headRefOid,
    last_push_at: (.commits | last.committedDate),
    coderabbit_walkthrough: ([.comments[] | select(.author.login == "coderabbitai")] | sort_by(.createdAt) | first),
    coderabbit_other:       ([.comments[] | select(.author.login == "coderabbitai")] | sort_by(.createdAt) | .[1:]),
    coderabbit_reviews:     [.reviews[]  | select(.author.login == "coderabbitai")],
    gemini_reviews:         [.reviews[]  | select(.author.login == "gemini-code-assist")],
    gemini_comments:        [.comments[] | select(.author.login == "gemini-code-assist")],
    codex_reviews:          [.reviews[]  | select(.author.login == "chatgpt-codex-connector")],
    codex_comments:         [.comments[] | select(.author.login == "chatgpt-codex-connector")],
    own_trigger_comments:   [.comments[] | select(
                              (.author.login != "coderabbitai" and .author.login != "gemini-code-assist" and .author.login != "chatgpt-codex-connector")
                              and (.body | test("^\\s*(/gemini review|@codex review|@coderabbitai resume)\\s*$"; "i"))
                            )]
  }'
```

> **重要**：主查询的 `coderabbit_walkthrough` 节点**不含** `updatedAt` 字段（GraphQL 默认不返回）。判 walkthrough 是否对当前 HEAD 编辑过，**必须**走副查询 A 拿 REST 的 `updated_at`。

**副查询 A**（REST issue comments，含 `updated_at`——CodeRabbit walkthrough 是否对当前 HEAD 编辑过的强信号）：

```bash
OWNER_REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
gh api "repos/${OWNER_REPO}/issues/<PR_NUMBER>/comments" \
  --jq '[.[] | select(.user.login == "coderabbitai[bot]")]
        | sort_by(.created_at) | first
        | {
            created_at,
            updated_at,
            is_ok:          (.body | test("No actionable comments were generated in the recent review")),
            is_paused:      (.body | test("(review[s]?\\s+paused|paused\\s+by\\s+coderabbit|automatic reviews are paused|paused\\s+for\\s+this\\s+PR)"; "i")),
            is_in_progress: (.body | test("(review in progress by coderabbit|currently processing new changes)"; "i")),
            actionable_count: (if (.body | test("Actionable comments posted:")) then (.body | capture("Actionable comments posted:\\s*(?<n>[0-9]+)") | .n) else null end)
          }'
```

**副查询 B**（PR reactions——Codex 无建议时只 👍 不留评论的路径）：

```bash
gh api "repos/${OWNER_REPO}/issues/<PR_NUMBER>/reactions" \
  --jq '[.[] | select(.user.login == "chatgpt-codex-connector[bot]") | .content]'
```

返回数组里有 `"+1"` 即 Codex 已 👍。

**副查询 C**（inline review comments——Gemini **和 Codex** 的具体建议都在这里，按 user 分组拉，alt 文本里有 severity 标签：Gemini 是 `high` / `medium` / `low` / ...，Codex 是 `P0 Badge` / `P1 Badge` / ...）：

```bash
gh api "repos/${OWNER_REPO}/pulls/<PR_NUMBER>/comments" \
  --jq '[.[] | select(.user.login | test("(coderabbitai|gemini-code-assist|chatgpt-codex-connector)\\[bot\\]$"))]
        | group_by(.user.login)
        | map({
            user: .[0].user.login,
            items: map({
              path,
              commit_id,
              created_at,
              severity_alt: (.body | capture("!\\[(?<s>[^\\]]+)\\]")? | .s // null),
              is_ack:       ((.body | test("<!--\\s*<review_comment_addressed>")) or (.body | test("^### Summary"))),
              body_head:    (.body | .[0:200])
            })
          })'

> **关键**：判定"本轮新 inline"必须用 `created_at > last_push_at` 过滤——**不要用 `commit_id == head`**。实测发现 CodeRabbit 在新 HEAD 重审时，GitHub 会把它旧 inline 的 `commit_id` 跟着新 HEAD 推进（推测是 in-place edit 或 thread 关联），用 `commit_id == head` 会把上一轮的 inline 也算进本轮判定。`created_at` 是评论真实创建时间，对每条 inline 独立稳定。
```

把所有查询结果连同 `head` 和最新时间戳**记在对话上下文里**，不要落盘。

### 2. 对每个启用的 reviewer 决定动作

按下表问一遍，命中即执行；同一轮可以并行处理多个 reviewer：

| 当前状态 | 动作 |
|---|---|
| 副查询 A 的 `is_paused == true`，且**副查询 A 的 `updated_at` 之后未发过 `@coderabbitai resume`**（从 `own_trigger_comments` 里**仅过滤 body 等于 `@coderabbitai resume` 的条目**，看最新一条的 `createdAt` 是否早于 `updated_at`；为空则按"未发过"处理。**不要**用"最新一条"统计混合所有命令——`/gemini review` 的最新发送不应阻止 `@coderabbitai resume`） | 发 `@coderabbitai resume` |
| Gemini 启用，最近一次 push 之后 Gemini 没新 review 也没发过 `/gemini review` | 发 `/gemini review` |
| Codex 启用且按 §「Codex 触发决策」判断认为该叫 | 发 `@codex review` |
| 还有 reviewer 在最新 HEAD 上没出结果 | 等下一轮（见 §「polling 节奏」） |
| 至少一个 reviewer 给出新 actionable 意见 | 进步骤 3 |
| 所有启用的 reviewer 都对当前 HEAD 给绿灯（见 §「怎么算已通过」） | 退出并简短汇报 |

**去重原则**：同一 HEAD 上 `/gemini review` 和 `@codex review` 各只能发一次——查 `own_trigger_comments` 里这条命令的 `createdAt` 是否在 `last_push_at` 之后。

### 3. 收意见 → 交给 receiving-code-review

把所有 reviewer 的新意见**合并一次**通过 Skill 工具调用 `superpowers:receiving-code-review`，不要逐家分调。

> **为什么合并：** 不同 reviewer 经常对同一段代码给覆盖性或冲突建议。合并后 receiving-code-review 在一次心智周期内做去重和仲裁；分开调容易让同一处代码被反复改、最后绕回原点。

receiving-code-review 返回后回步骤 1。它自己负责实施修复、向 reviewer 写回复（含 inline review reply）、记录 pushback——本 skill 只重新拉数据看是否产生了新 HEAD 或新一轮 review。

## 关键判断

### 怎么判 "Reviewer 已审过当前 HEAD"

每家信号源不同：

- **CodeRabbit**：副查询 A 返回的 walkthrough `updated_at` 晚于 `last_push_at`（首条评论被重新编辑了）
- **Gemini**：`gemini_reviews` 里有 review 的 `submittedAt` 晚于 `last_push_at`
- **Codex**：`codex_reviews[*].body` 含 `**Reviewed commit:** <HEAD前缀>`（前 7-10 位匹配即可）；或副查询 B 显示 Codex 有 `+1` reaction

### 怎么算 "actionable"

优先看 bot 自己给的 explicit signal：

- **CodeRabbit** → 副查询 A 的 `is_ok == true`（CodeRabbit 显式 OK 文案）或 `actionable_count == "0"`——**无** actionable；否则**具体建议在 inline review comments**（不在 walkthrough body），需另拉 REST `/pulls/<PR>/comments` 里 `coderabbitai[bot]` 的条目，body 开头常带 `_⚠️ Potential issue_` / `_🟠 Major_` / `_🛠️ Refactor suggestion_` / `_💡 Verification agent_` 等标签——非 nit 级别都算 actionable
- **Gemini** → 副查询 C 里 `gemini-code-assist[bot]` 的 inline items，`severity_alt` 含 `high` / `medium` / `critical` 算 actionable；`low` / `nit` / `style` 不算
- **Codex** → 副查询 C 里 `chatgpt-codex-connector[bot]` 的 inline items（review summary body 只是模板，**具体建议在 inline**）；`severity_alt` 是 `Pn Badge` 形式（如 `P1 Badge`），n 越小越严重——按判断力分级，通常 P0 / P1 算 actionable，P2 / P3 视场景而定。若 Codex 在当前 HEAD 上只有 `+1` reaction 没留 inline comment，**不是** actionable（这是它的"通过"信号）

**Acknowledgment 例外**：副查询 C 里 `is_ack == true` 的 inline 是 reviewer 对前次 fix / inline reply 的**确认回复**（CR 用 `<!-- <review_comment_addressed> -->` HTML 标记自家 ack；Codex 用 body 开头 `### Summary` 表示 cross-check 总结），**不计入** actionable。

review state == `APPROVED` 一律算无 actionable。

### 怎么算 "已通过"

当前 HEAD 下，每个启用的 reviewer 满足以下之一：

- **CodeRabbit**：副查询 A 的 `is_ok == true`（或 `actionable_count == "0"`），**或**副查询 C 里 `coderabbitai[bot]` 在本轮（`created_at > last_push_at`）的 inline 全是 `is_ack == true`，且 `updated_at > last_push_at`，且 **`is_in_progress == false`**（in-progress 时表示 CR 还在审，先回 poll 不要急于判定通过）
- **Gemini**：副查询 C 里 `gemini-code-assist[bot]` 在当前 HEAD 上的 inline items severity 全是 `low/nit/style`/为空，**或剩下的都是 `is_ack == true`**，且 `gemini_reviews` 最近一条 `submittedAt > last_push_at`
- **Codex**：满足以下任一即可（按 Codex 的三种 ack 模式）：
  - 副查询 B 出现 `+1`（reaction 路径）
  - `codex_reviews` 里最新一条 `submittedAt > last_push_at` 且 body 含 `Reviewed commit` 匹配当前 HEAD，副查询 C 里 `chatgpt-codex-connector[bot]` 在本轮（`created_at > last_push_at`）无非 ack inline
  - `codex_reviews` 里最新一条 `submittedAt > last_push_at` 且 `body == ""` 且本轮无新 inline（空 body review 路径）
- 或该 reviewer 被用户临时禁用

### CodeRabbit pause 的识别

CodeRabbit 状态全靠**反复编辑 walkthrough**。副查询 A 的 `is_paused` 已封装了不区分大小写的关键词匹配：

- `review paused` / `reviews paused`（单复数都覆盖；实测 CR 实际用 `<!-- ... review paused by coderabbit.ai -->` 这种 HTML 注释，单数形式）
- `paused by coderabbit`（HTML 注释 marker，最强信号）
- `automatic reviews are paused`
- `paused for this PR`

如果上述都没命中但仍怀疑 pause（例如历史上有 `@coderabbitai pause` 被发过且之后再无 walkthrough 编辑 / `updated_at` 没动），看具体 walkthrough body 自己判断，必要时扩展 `is_paused` 的正则。

发 `@coderabbitai resume` 后等 ~30s 让 bot 接管。

### Codex 触发决策

Codex **不**自动跟 commit，是否手动触发 `@codex review` 由 Claude 按必要性自行判断。可参考但不限于以下维度：

- 用户的明确意图（提到 codex 就基本是要叫）
- CodeRabbit 与 Gemini 的意见是否存在重大分歧，需要第三方仲裁
- 本次 PR 改动面是否值得多一份独立审查（敏感面、跨模块影响、新增依赖等）
- 是否已经在本 HEAD 上叫过（去重）

没必要就跳过。这是判断题，不是 checklist。

### polling 节奏

AI reviewer 都有 cold-start 延迟，刚 push 就猛 poll 是浪费：

- **第一轮**：刚 push / 刚进入循环 → **先等 3 分钟**再做第一次 poll
- **之后**：每 60 秒 poll 一次
- **超 15 分钟无动静**：停下来问用户要不要跳过这个 reviewer 或重发触发命令——不要无脑等也不要自动 retry，重 retry 会刷出评论垃圾

要纯后台跑就叠加 `loop` skill 或 `ScheduleWakeup`。

## 故障处理

- **某个 reviewer 一直不回**：bot 可能挂了 / 配额满。**15 分钟**没动静就停下来问用户怎么处理（与 §「polling 节奏」中的上限一致）。
- **bot 报错（"Internal error" / "Token limit exceeded"）**：把错误内容贴给用户，问要不要发 `@coderabbitai full review` / `/gemini review` 强制重跑。
- **gh 401/403**：让用户跑 `gh auth refresh -s repo`。
- **CI 失败**：CodeRabbit 会等 GitHub Checks 跑完再继续；CI 红时 review 可能不来——先帮用户修 CI，AI reviewers 自然会接上。

## 与其他 skill 的边界

| 任务 | 用哪个 |
|---|---|
| 创建 PR | `commit-commands:commit-push-pr` |
| 回应 / 实施 / 反驳 review 意见 | `superpowers:receiving-code-review` |
| 验证修复是否真的解决问题 | `superpowers:verification-before-completion` |
| **盯多 AI reviewer 的循环节奏** | **本 skill** |

本 skill 只做调度——什么时候 poll、什么时候 resume/触发、什么时候把控制权交给 receiving-code-review、什么时候结束循环。**不**负责"如何回应意见"和"如何验证修复"。
