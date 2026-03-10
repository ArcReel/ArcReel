## ADDED Requirements

### Requirement: 同步 Agent 对话端点
系统 SHALL 提供 `POST /api/v1/agent/chat` 同步端点，接收用户消息并返回完整的 Agent 回复。

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

### Requirement: 对话内容格式
响应 SHALL 包含 Agent 生成的纯文本回复，去除内部工具调用细节，仅保留面向用户的回复内容。

#### Scenario: Agent 使用工具后回复
- **WHEN** Agent 内部调用了工具（如生成剧本）并产生文本回复
- **THEN** 响应的 `reply` 字段仅包含面向用户的文本，不暴露工具调用细节
