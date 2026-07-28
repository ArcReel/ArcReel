## MODIFIED Requirements

### Requirement: 同步 Agent 对话端点
系统 SHALL 提供 `POST /api/v1/agent/chat` 同步端点，接收用户消息（含可选图片附件）并返回完整的 Agent 回复。

#### Scenario: 新会话对话
- **WHEN** 已认证用户调用 `POST /api/v1/agent/chat`，提供 `project_name` 和 `message`，不传 `session_id`
- **THEN** 系统创建新会话，执行 Agent 对话，返回 `session_id`、`reply`（完整文本）和 `status: "completed"`

#### Scenario: 复用现有会话
- **WHEN** 已认证用户调用该端点并提供有效的 `session_id`
- **THEN** 系统在该会话上下文中继续对话，返回回复

#### Scenario: 项目不存在
- **WHEN** 提供的 `project_name` 对应的项目不存在
- **THEN** 系统返回 404

#### Scenario: 响应超时
- **WHEN** Agent 处理超过 120 秒
- **THEN** 系统返回已收集的部分响应，`status` 为 `"timeout"`

#### Scenario: 携带图片附件发送消息
- **WHEN** 已认证用户调用 `POST /api/v1/assistant/sessions/{id}/messages`，请求体包含 `content`（文字）和 `images`（最多 5 个 base64 图片对象）
- **THEN** 系统将文字与图片组合为 multimodal 消息传递给 Agent，Agent 可感知图片内容并作出回复
