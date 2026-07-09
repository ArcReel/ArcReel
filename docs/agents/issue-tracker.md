# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

## Spec 与细分 issue

Spec（原 PRD）和按 Spec 拆分出的实现 issue 必须在**列表视图**就能区分与溯源，不能只靠正文：

### Spec issue

- 标题统一以 `Spec: ` 开头，例如 `Spec: 集成 TTS 文本转语音 —— …`
- 打 `Spec` 标签。`to-spec` 发布时同时加 `Spec` 与 `ready-for-agent` 两个标签

### 细分（实现）issue

- 标题**末尾**加归属尾缀 `[Spec #<父编号>]`，例如 `分集账本：project.json schema 扩展与存量项目启动回填 [Spec #751]` —— 任何列表视图（`gh issue list`、Web、通知）都能直接看出归属
- 正文保留 `## Parent` 一节引用父 Spec（既有模板不变，尾缀是补充而非替代）
- 同时挂为父 Spec 的 **GitHub 原生 sub-issue**，让父 Spec 显示完成进度条：

```bash
# 1. 取细分 issue 的 database id（不是 issue 编号）
sub_id=$(gh api repos/{owner}/{repo}/issues/<细分编号> --jq .id)
# 2. 挂到父 Spec 下（-F 传整数）
gh api repos/{owner}/{repo}/issues/<父编号>/sub_issues -F sub_issue_id=$sub_id
```

`to-tickets` 拆分 Spec 时，每个 issue 创建后都要补这两步（标题尾缀在创建时直接写入标题）。

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body. `gh issue create --label wayfinder:map`.
- **Child ticket**: an issue linked to the map as a GitHub sub-issue (`gh api` on the sub-issues endpoint). Where sub-issues aren't enabled, add the child to a task list in the map body and put `Part of #<map>` at the top of the child body. Labels: `wayfinder:<type>` (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: GitHub's **native issue dependencies** — the canonical, UI-visible representation. Add an edge with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`, where `<blocker-db-id>` is the blocker's numeric **database id** (`gh api repos/<owner>/<repo>/issues/<n> --jq .id`, _not_ the `#number` or `node_id`). GitHub reports `issue_dependencies_summary.blocked_by` (open blockers only — the live gate). Where dependencies aren't available, fall back to a `Blocked by: #<n>, #<n>` line at the top of the child body. A ticket is unblocked when every blocker is closed.
- **Frontier query**: list the map's open children (`gh issue list --state open`, scoped to the map's sub-issues / task list), drop any with an open blocker (`issue_dependencies_summary.blocked_by > 0`, or an open issue in the `Blocked by` line) or an assignee; first in map order wins.
- **Claim**: `gh issue edit <n> --add-assignee @me` — the session's first write.
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append a context pointer (gist + link) to the map's Decisions-so-far.
