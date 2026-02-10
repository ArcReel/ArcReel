# 助手工具审批流改造建议（问题1）

## 背景

当前 `can_use_tool` 仅对 `AskUserQuestion` 走用户交互，其余工具默认 `allow`。  
这会让 Web 端失去对高风险工具调用（如 `Bash`、`Write`、`Edit`）的可见性与控制能力，不符合官方关于“应用侧承接审批请求并返回决策”的推荐实践。

## 目标

1. 所有非自动放行工具请求都能在前端展示为“待审批”。
2. 用户可执行 `允许 / 拒绝 / 允许并修改参数` 三类决策。
3. 决策结果回流到 `can_use_tool`，不中断当前流式会话。
4. 审批请求可重连恢复（页面刷新或 SSE 重连后不丢审批状态）。

## 建议架构

## 1) 事件模型统一

新增一种运行时消息（建议）：

- `tool_approval_request`
  - `request_id`
  - `tool_name`
  - `input`
  - `created_at`
  - `session_id`
  - `risk_level`（可选，前端分级展示）

前端 SSE 新增事件（建议）：

- `approval`：承载 `tool_approval_request`

> 也可复用 `question` 事件通道，但建议独立事件类型，避免 `AskUserQuestion` 与工具审批语义混淆。

## 2) SessionManager 侧改造

在 `_build_can_use_tool_callback` 中：

1. 识别 `AskUserQuestion`（保持现有逻辑）。
2. 对其他工具：
   - 若命中“自动放行规则”则直接 `PermissionResultAllow`。
   - 否则创建 `pending_approval`，向消息缓冲区写入 `tool_approval_request`，并 `await` 用户决策。
3. 用户决策返回后：
   - `allow` -> `PermissionResultAllow(updated_input=...)`
   - `deny` -> `PermissionResultDeny(message=..., interrupt=...)`

新增 `ManagedSession.pending_approvals` 与对应：

- `add_pending_approval()`
- `resolve_pending_approval()`
- `cancel_pending_approvals()`
- `get_pending_approval_payloads()`

## 3) AssistantService / Snapshot 侧

`snapshot` 返回中新增：

- `pending_approvals: []`

SSE 重连首包 `snapshot` 应包含未决审批项，确保前端可恢复审批 UI。

## 4) Router 侧

新增接口（建议）：

- `POST /api/v1/assistant/sessions/{id}/approvals/{request_id}/decision`
  - 请求体：
    - `decision`: `allow | deny`
    - `updated_input`（可选）
    - `message`（拒绝原因，可选）
    - `interrupt`（可选）

## 5) 前端侧

`use-assistant-state` 新增：

- `assistantPendingApproval`
- `assistantApproving`
- `handleApproveToolRequest(requestId, decisionPayload)`

`AssistantMessageArea` 增加审批卡片区：

1. 显示工具名、关键参数摘要、风险标记。
2. 支持允许/拒绝。
3. 支持高级模式编辑 `updated_input`（JSON）。

## 默认策略建议

默认自动放行（可配置）：

- `Read`
- `Glob`
- `Grep`
- `LS`

默认需审批：

- `Bash`
- `Write`
- `Edit`
- `MultiEdit`
- 其他具副作用或外部访问能力工具

## 兼容与迁移

1. 第一阶段：后端先支持 `pending_approvals` 与 decision API，前端灰度接入。
2. 第二阶段：关闭“非 AskUserQuestion 全量默认放行”。
3. 第三阶段：加入规则配置（项目级 / 会话级）。

## 验收清单

1. 触发 `Bash` 时，前端出现审批卡片而非静默执行。
2. 点击“拒绝”后，模型收到拒绝反馈并继续后续推理。
3. 刷新页面后，未处理审批仍能在 `snapshot.pending_approvals` 恢复。
4. 流式会话不中断，`status` 事件仍按 `ResultMessage` 收敛。
5. `AskUserQuestion` 流程不回归。
