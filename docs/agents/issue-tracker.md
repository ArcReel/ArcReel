# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

## PRD 与细分 issue

PRD（产品需求文档）和按 PRD 拆分出的实现 issue 必须在**列表视图**就能区分与溯源，不能只靠正文：

### PRD issue

- 标题统一以 `PRD: ` 开头，例如 `PRD: 集成 TTS 文本转语音 —— …`
- 打 `PRD` 标签（紫色）。`to-prd` 发布时同时加 `PRD` 与 `ready-for-agent` 两个标签
- 过滤所有 PRD：`gh issue list --label PRD`

### 细分（实现）issue

- 标题**末尾**加归属尾缀 `[PRD #<父编号>]`，例如 `分集账本：project.json schema 扩展与存量项目启动回填 [PRD #751]` —— 任何列表视图（`gh issue list`、Web、通知）都能直接看出归属
- 正文保留 `## Parent` 一节引用父 PRD（既有模板不变，尾缀是补充而非替代）
- 同时挂为父 PRD 的 **GitHub 原生 sub-issue**，让父 PRD 显示完成进度条：

```bash
# 1. 取细分 issue 的 database id（不是 issue 编号）
sub_id=$(gh api repos/{owner}/{repo}/issues/<细分编号> --jq .id)
# 2. 挂到父 PRD 下（-F 传整数）
gh api repos/{owner}/{repo}/issues/<父编号>/sub_issues -F sub_issue_id=$sub_id
```

`to-issues` 拆分 PRD 时，每个 issue 创建后都要补这两步（标题尾缀在创建时直接写入标题）。

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.
