# Herdr 跨 harness 委派

调用已安装的 `herdr` skill 执行所有 Herdr CLI 操作；本页只定义 AFK workflow 与 Herdr transport 的衔接。

## 委派边界

- 同 harness teammate 使用 harness 原生团队能力；跨 harness teammate 使用 Herdr。teammate 自己需要嵌套 agent 时仍使用其 harness 原生能力
- 在批次计划中列出每个阶段的 harness 与模型；按 [model-selection.md](model-selection.md) 选择模型，Herdr 只承载已决定的 agent kind 和 native model 参数
- Herdr teammate 与原生 teammate 统一占用现有并发槽位
- Herdr lifecycle 状态只用于唤醒 team-lead。阶段完成仍以契约交付物、handoff、Git / GitHub 事实与第三步的机械核验为准

## 批次拓扑

组建团队时为本批次创建一个名为 `afk:<batch-id>` 的 tab，并在其中安排全部跨 harness teammate。记录本批创建的对象，收尾时只关闭这些对象。

启动 teammate 时：

1. 令 pane cwd 指向该阶段实际工作的 worktree
2. 通过 native agent args 仅把 `<repo-root>/.afk/<batch-id>/` 加入额外可写目录，使 teammate 能追加 handoff；沿用 harness 已有的 permission / sandbox 配置
3. 传入对应的 [spawn prompt](spawn-prompts.md)，并补充 batch-id、Herdr agent name、agent pane ID 与 team-lead pane ID

## 反向通知

`herdr agent prompt` 不携带发送者身份。teammate 向 team-lead 发送自然语言 prompt 时，显式写明：

- batch-id、issue、阶段
- Herdr agent name 与 agent pane ID
- 类型：`handoff` 或 `request`
- 一句摘要；`handoff` 同时给出交付物或 handoff 的绝对路径

只在现有契约要求“报告 team-lead”或“询问 team-lead”时发送：

- `handoff`：先落下对应的持久事实或交付物（如已创建的 worktree、已追加的阶段 handoff），再通知 team-lead
- `request`：遇到需要 team-lead 裁决或处理的故障时通知；team-lead 按现有裁决分类法处理，并用自然语言回复该 agent

prompt 是唤醒和定位线索，完整内容留在既有交付物与 handoff 中。Herdr 拓扑 ID 和消息不写入账本；续跑与接管仍按主流程既有事实源重建。
