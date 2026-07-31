# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |
| — (local extension)        | `parked`             | Evaluated, deliberately parked outside the state machine; excluded from the untriaged bucket |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## 看板投影

[org Project「ArcReel」](https://github.com/orgs/ArcReel/projects/2)的 Status 字段由 repo 侧事实**单向派生**，板上拖卡不作数（事件触发 + 每日对账纠正，实现在 `scripts/project_status_sync.py` 与 `.github/workflows/project-status-sync.yml`）。派生优先级从高到低：

| 条件                                  | Status                                    |
| ------------------------------------- | ----------------------------------------- |
| 带 `Spec` / `parked` 标签             | 不占列（Status 清空，主视图 filter 排除） |
| issue 已关闭                          | Done                                      |
| 存在 open 的 closing PR               | In review                                 |
| 有 assignee                           | In progress                               |
| `ready-for-agent` / `ready-for-human` | Ready · agent / Ready · human             |
| `needs-info`                          | Needs info                                |
| `needs-triage` 或无任何匹配（兜底）   | Inbox                                     |
