# AI Reviewer 速查

本 skill 的循环决策依赖这三家 bot 的状态信号 —— 每家表达"我审过了 / 我有意见 / 我通过了"的方式都不一样,需用对应方式解读。读 SKILL.md 时同步参考这张表。

## 三家概览

| Reviewer | GraphQL `author.login` | REST `user.login` | 自动跟新 commit | 状态表达方式 | 触发命令 |
|---|---|---|---|---|---|
| CodeRabbit | `coderabbitai` | `coderabbitai[bot]` | **是** | **反复改写首条评论(walkthrough)**:`updated_at` 会被推后,body 开头带 `<!-- ... summarize by coderabbit.ai -->` HTML 注释。OK 时 body 首行是 `No actionable comments were generated in the recent review. 🎉`。其余 reply 为独立会话评论 | `@coderabbitai resume` / `review` / `full review` |
| Gemini Code Assist | `gemini-code-assist` | `gemini-code-assist[bot]` | 否 | **review summary** 每次发一条新评论(body 以 `## Code Review` 开头,是整个 PR 的总结,**里面会有 actionable** —— 不能只看 inline);**严重度标签在 inline review comments 里**,body 开头是 `![high](https://www.gstatic.com/codereviewagent/high-priority.svg)` 这种 markdown image | `/gemini review` |
| OpenAI Codex | `chatgpt-codex-connector` | `chatgpt-codex-connector[bot]` | **看仓库配置**:默认需手动 `@codex review`;若仓库开启 PR 自动 review,Codex 会自动跟新 commit | **三种 ack 模式** —— 见下文 | `@codex review` |

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
