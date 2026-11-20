# 分流标签

各 skill 使用下列 GitHub 标签。每个待分流对象的下一步由状态标签决定，巡检的动作与例外见[无人值守 triage](triage-sweep.md)。

| 标签 | 下一步 |
| --- | --- |
| `needs-triage` | 维护者判断价值、方向或优先级。 |
| `needs-info` | 等待提问者补充信息，或等待 PR 作者提交修改。 |
| `ready-for-agent` | Agent 可按已明确的范围实施。 |
| `ready-for-human` | 维护者负责处理或合并。 |
| `wontfix` | 记录原因后关闭。 |
| `parked` | 已评估并搁置，退出待分流队列。 |

`bug`、`enhancement` 是类别标签。巡检建议合并 PR 时另加 `merge-ready`。
