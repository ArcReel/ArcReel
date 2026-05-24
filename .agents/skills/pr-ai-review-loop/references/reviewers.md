# AI Reviewer 速查

Skill 的运行决策依赖这三家 bot 的状态信号——每家表达"我审过了 / 我有意见 / 我通过了"的方式不一样,必须用对应方法读。读 SKILL.md 时同步看一眼这张表。

## 三家概览

| Reviewer | GraphQL `author.login` | REST `user.login` | 自动跟新 commit | 状态表达方式 | 触发命令 |
|---|---|---|---|---|---|
| CodeRabbit | `coderabbitai` | `coderabbitai[bot]` | **是** | **反复编辑首条评论(walkthrough)**:`updated_at` 会被推后,body 开头有 `<!-- ... summarize by coderabbit.ai -->` HTML 注释。OK 时 body 首行:`No actionable comments were generated in the recent review. 🎉`。其余 reply 是另算的会话评论 | `@coderabbitai resume` / `review` / `full review` |
| Gemini Code Assist | `gemini-code-assist` | `gemini-code-assist[bot]` | 否 | **review summary** 每次发新评论(body 以 `## Code Review` 开头,是 PR 总结,**含有 actionable** —— 不能只看 inline);**严重度标签在 inline review comments**:body 开头是 `![high](https://www.gstatic.com/codereviewagent/high-priority.svg)` 这种 markdown image | `/gemini review` |
| OpenAI Codex | `chatgpt-codex-connector` | `chatgpt-codex-connector[bot]` | **按仓库配置**:默认要手动 `@codex review`;若仓库开启了 PR 自动 review,Codex 会自动跟新 commit | **三种 ack 模式**——见下方 | `@codex review` |

## Codex 三种 ack 模式

Codex 表达"对当前 HEAD 无意见"有三条路径,任一命中即视为已 ack:

1. **inline review with body**:`codex.reviews` 里最新一条 body 开头 `### 💡 Codex Review`,含 `**Reviewed commit:** <SHA>`。短 SHA 前 7-10 位匹配当前 HEAD 即认
2. **PR-level +1 reaction**:`codex.reactions` 里存在 `content == "+1"` **且** `created_at > last_push_at`(必须本轮 push 之后留的 👍——旧 reaction 不算)。Codex 用这条表示"看过了无话可说"
3. **empty-body review**:`codex.reviews` 里最新一条 `submittedAt > last_push_at` **且** `state == "COMMENTED"` **且** `body == ""`,且本轮无新 inline。Codex 在新 HEAD 自动跟时若无新意见,可能用空 body review 代替 reaction

## REST vs GraphQL 命名陷阱

`poll.sh` 的 JSON 输出已经统一了 key——`inline_comments_by_user` 用 REST 的带 `[bot]` 名(因为 inline 数据本身来自 REST),其余顶层字段用 GraphQL 的不带 `[bot]` 名。**但**当你直接读 SKILL.md 之外的 jq 时:

| 数据源 | 字段路径 | 是否带 `[bot]` |
|---|---|---|
| `gh pr view --json reviews,comments,...` (GraphQL) | `.author.login` | **不带** —— 如 `coderabbitai` |
| `gh api repos/.../pulls/.../comments` (REST inline) | `.user.login` | **带** —— 如 `coderabbitai[bot]` |
| `gh api repos/.../issues/.../reactions` (REST) | `.user.login` | **带** —— 如 `chatgpt-codex-connector[bot]` |

混用必踩坑。两个端的字符串不通用。

## bot 改名查询

bot 改名时跑这条拿最新 GraphQL 名:

```bash
gh pr view <PR> --json reviews,comments \
  --jq '[.reviews[].author.login, .comments[].author.login] | unique'
```

REST 名规则:GraphQL 名 + `[bot]` 后缀。同时改 `references/reviewers.md` + `scripts/poll.sh` 的两处 select 语句。

## 其它 bot

`github-code-quality[bot]`(GitHub 自带静态分析)、`codecov[bot]`(覆盖率)等默认**不**纳入主循环决策——它们的输出通常是死板的 nit / 数字,没有"等待"或"重审"概念。它们的 inline 意见在调用 receiving-code-review 时被一并看到。

用户可随时让某个 reviewer 进/出循环("这次别管 gemini"、"叫上 codex"、"也看看 code-quality"),按上下文意图执行。
