# GitHub 议题

Spec 和 ticket 统一使用本仓库的 GitHub Issues 管理，并通过 `gh` CLI 操作。运行 `to-spec`、`to-tickets`、`triage` 或 `wayfinder` 时，以下仓库约定优先于 skill 的通用模板。标签含义见[分流标签](triage-labels.md)。

## Spec 与 ticket

| 类型 | 标题与标签 | 归属关系 |
| --- | --- | --- |
| Spec | 标题以 `Spec: ` 开头，只打 `Spec` 标签 | 记录整体目标、范围与验收标准，执行时认领其拆出的 ticket |
| ticket | 从 Spec 拆出的 ticket 在标题末尾加 `[Spec #<父编号>]` | 正文的 `## Parent` 引用父 Spec，同时设为 GitHub 原生 sub-issue |

- 使用 gh 2.94 或更新版本。创建子议题用 `gh issue create --parent <父编号>`，为已有议题补父子关系用 `gh issue edit <父编号> --add-sub-issue <子编号>`。
- 登记阻塞关系使用 `gh issue edit <待办编号> --add-blocked-by <阻塞编号>`。父子关系表达归属，阻塞关系决定实施顺序。
- 发布多条议题后逐条读回标题和正文，核对它们与已确认的拆分一一对应。多行正文经 `--body-file` 提交。
- 可认领项为未分配且带 `ready-for-agent` 的开放议题。用 `gh issue list --state open --label ready-for-agent --search "no:assignee"` 查找，开工前用 `gh issue edit <编号> --add-assignee @me` 认领。保留原分流标签。
- 按编号读取用 `gh issue view <编号> --json number,title,body,labels,comments`。裸编号在 issue 和 PR 间共用，类型不明时先用 `gh pr view <编号>` 确认。

## 外部 PR 分流

**PRs as a request surface: yes.** 外部 PR 与 issue 使用同一套分流标签。手动分流和无人值守巡检都按[当前范围规则](triage-sweep.md#核对当前范围)核对关闭依据。

查看外部 PR 时，读取 `gh pr view <编号> --comments` 和 `gh pr diff <编号>`。筛选候选 PR 时保留作者的 `author_association` 和 `user.type` 字段，按巡检定义识别外部投稿。

## Wayfinding operations

- Map 是带 `wayfinder:map` 的 issue，正文记录 Notes、Decisions-so-far 和 Fog。各 ticket 按上述原生父子关系挂接，标签为 `wayfinder:<type>`（`research`、`prototype`、`grilling`、`task`）。
- 查找下一项时，从 map 的开放子议题中筛出未认领且无开放 blocker 的项，再按 map 顺序选择。GitHub 汇总字段 `issue_dependencies_summary.blocked_by` 只计开放 blocker。
- 选中 ticket 后，用 `gh issue edit <编号> --add-assignee @me` 认领，再开工。完成后把结论写在 ticket 评论里，关闭 ticket，并把结论和链接补到 map 的 Decisions-so-far。
