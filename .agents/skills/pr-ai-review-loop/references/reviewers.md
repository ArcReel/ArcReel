# AI Reviewer 速查

本 skill 的循环决策依赖三家 bot 的状态信号。每家表达"已审查 / 有意见 / 已通过"的方式不同,需按对应方式解读。

## 三家概览

| Reviewer | GraphQL `author.login` | REST `user.login` | 自动 review 时机 | 状态表达方式 | 触发命令 |
|---|---|---|---|---|---|
| CodeRabbit | `coderabbitai` | `coderabbitai[bot]` | PR opened 及后续每次 push 均自动 | **反复改写首条评论(walkthrough)**:`updated_at` 会被推后,body 开头带 `<!-- ... summarize by coderabbit.ai -->` HTML 注释。通过时 body 首行为 `No actionable comments were generated in the recent review. 🎉`。其余 reply 为独立会话评论 | `@coderabbitai resume` / `review` / `full review` |
| Gemini Code Assist | `gemini-code-assist` | `gemini-code-assist[bot]` | **仅 PR opened 时自动**(5 分钟内出结果);后续 push 不自动跟,需手动 `/gemini review` | **review summary** 每次发一条新评论(body 以 `## Code Review` 开头,涵盖整个 PR 的总结,**其中可能包含 actionable 建议** —— 不能只看 inline);**严重度标签位于 inline review comment 中**,body 开头形如 `![high](https://www.gstatic.com/codereviewagent/high-priority.svg)` 的 markdown 图片 | `/gemini review` |
| OpenAI Codex | `chatgpt-codex-connector` | `chatgpt-codex-connector[bot]` | **取决于仓库配置**:默认需手动 `@codex review`;若仓库开启 PR 自动 review,Codex 会自动跟新 commit | **三种 ack 模式**,见下文 | `@codex review` |

### Gemini 的 opened 与 synchronize 行为差异

Gemini Code Assist 是事件驱动的 review bot,两种事件下行为不同,本 skill 的决策需要区分:

- **PR opened**:GitHub App 自动 review,通常 5 分钟内提交首条 review summary。本 skill 首轮 poll 应预留至少 180 秒 cold-start 等待,期间**不应**手动发送 `/gemini review`——重复触发会让 Gemini 再扫一遍,既消耗 quota,也容易引入第一次未提及的边缘建议。
- **synchronize**(向已存在的 PR push 新 commit):Gemini 不会自动重新 review。当前 HEAD 上若需要 Gemini 重新审查,需要手动发送 `/gemini review`。

判别方法(按 `poll.sh` 输出的 `pr_created_at` 与 `gemini.reviews`):

- `gemini.reviews` 完全为空且 `pr_created_at` 距今不足 5 分钟 → cold-start 窗口内,等待即可
- `gemini.reviews` 完全为空且 `pr_created_at` 距今已超过 5 分钟,且 `own_trigger_comments` 中不存在 `/gemini review`(或存在但最大 `createdAt ≤ last_push_at`)→ cold-start fallback:自动 review 未在窗口内出现(可能失败或被跳过),手动发送 `/gemini review`
- `gemini.reviews` 非空但最新一条 `submittedAt < last_push_at`,且 `own_trigger_comments` 中不存在 `/gemini review`(或存在但最大 `createdAt ≤ last_push_at`)→ synchronize 场景,手动发送 `/gemini review`

## Codex 三种 ack 模式

Codex 表达"对当前 HEAD 没意见"有三条路径,任一命中都算 ack:

1. **inline review with body**:`codex.reviews` 最新一条,body 开头 `### 💡 Codex Review`,含 `**Reviewed commit:** <SHA>`。短 SHA 前 7-10 位与当前 HEAD 匹配即算
2. **PR-level +1 reaction**:`codex.reactions` 里有 `content == "+1"` **且** `created_at > last_push_at`(必须是本轮 push 之后留的 👍,旧的不算)。Codex 用这条表示"看过了没话说"
3. **empty-body review**:`codex.reviews` 最新一条 `submittedAt > last_push_at` **且** `state == "COMMENTED"` **且** `body == ""`,且本轮没有新 inline。Codex 自动跟新 HEAD 但无新意见时,可能用空 body review 代替 reaction

## REST vs GraphQL 命名陷阱

`poll.sh` 的 JSON 输出已统一 key 命名 —— `inline_comments_by_user` 用 REST 的带 `[bot]` 名(因为 inline 数据本就来自 REST),其它顶层字段用 GraphQL 的不带 `[bot]` 名。**不过**直接写 SKILL.md 之外的 jq 时:

| 数据源 | 字段路径 | 带不带 `[bot]` |
|---|---|---|
| `gh pr view --json reviews,comments,...` (GraphQL) | `.author.login` | **不带** —— 比如 `coderabbitai` |
| `gh api repos/.../pulls/.../comments` (REST inline) | `.user.login` | **带** —— 比如 `coderabbitai[bot]` |
| `gh api repos/.../issues/.../reactions` (REST) | `.user.login` | **带** —— 比如 `chatgpt-codex-connector[bot]` |

两边的字符串不通用。

## 查询 bot 新名称

bot 改名后用这条查最新 GraphQL 名:

```bash
gh pr view <PR> --json reviews,comments \
  --jq '[.reviews[].author.login, .comments[].author.login] | unique'
```

REST 名规则:GraphQL 名 + `[bot]` 后缀。同步修改 `references/reviewers.md` 和 `scripts/poll.sh` 的两处 select 语句。

## 其它 bot

`github-code-quality[bot]`(GitHub 自带静态分析)、`codecov[bot]`(覆盖率)这类默认**不**纳入主循环 —— 它们输出大多是死板的 nit / 数字,没有"等待"或"重审"的概念。调 receiving-code-review 时会一并看到它们的 inline 意见。

用户可以随时让某家 reviewer 进/出循环("这次别管 gemini"、"叫上 codex"、"也看看 code-quality"),按用户的意图执行。
