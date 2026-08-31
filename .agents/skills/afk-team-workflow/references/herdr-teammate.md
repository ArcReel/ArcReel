# Herdr 跨 harness 委派

仅在 `HERDR_ENV=1` 时读本页，并先使用 Skill 工具调用 `herdr`。同 harness teammate 使用 harness 原生团队能力；跨 harness teammate 使用 Herdr。

## Workspace

1. 为批次创建一个 label 为 `afk:<batch-id>` 的 workspace，从 JSON 响应记住 workspace、tab 与 pane ID。本批的全部 Herdr teammates 都放在该 workspace，按 issue / stage 按需建 tab。
2. pane cwd 指向实际 worktree；native agent args 只增加 `<repo-root>/.afk/<batch-id>/` 为额外可写目录，并沿用 harness 的 permission / sandbox。
3. 启动 prompt 只注入 batch-id、当前 agent name / pane ID 与 team-lead pane ID，不注入其他 session 或 teammates 的寻址。

workspace 是拓扑与生命周期边界，不是消息权限边界。收尾时只关闭本批创建的 workspace。

## 反向通知

teammate 只向 prompt 注入的 team-lead pane ID 发消息。消息写明 batch-id、issue / stage、自己的 agent name / pane ID、`handoff` 或 `request` 类型与一句摘要。

- `handoff`：先写入 commit 或 handoff 等持久交付物，再通知路径或 SHA。
- `request`：仅用于真实业务取舍或故障裁决。

消息只做唤醒与定位；完整上下文留在 issue、commit 和 handoff。Herdr 拓扑 ID 不写入账本。
