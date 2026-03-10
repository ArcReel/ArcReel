## ADDED Requirements

### Requirement: Skill 定义文件动态渲染
系统 SHALL 提供 `GET /skill.md` 端点，读取 `public/skill.md.template` 模板文件，将 `{{BASE_URL}}` 占位符替换为请求者实际访问的 base URL（从 `Host` header 和 scheme 推断），返回渲染后的内容，无需认证。

#### Scenario: 访问 skill.md
- **WHEN** 任何客户端请求 `GET /skill.md`
- **THEN** 系统返回渲染后的 Skill 定义文件，其中所有 `{{BASE_URL}}` 已被替换为实际的服务地址（如 `https://my-arcreel.example.com`）

#### Scenario: 不同部署地址
- **WHEN** 用户自部署在 `http://192.168.1.100:1241` 并访问 `/skill.md`
- **THEN** 返回的文件中 API URL 为 `http://192.168.1.100:1241/api/v1/...`

### Requirement: Skill 工作流描述
skill.md SHALL 描述完整的使用工作流：创建项目 → 保存设置 → 多轮 Agent 对话 → 查看成果。

#### Scenario: OpenClaw 读取工作流
- **WHEN** OpenClaw Agent 加载 skill.md
- **THEN** 可从中获取完整的 API 调用序列和参数说明

### Requirement: Skill 工具定义
skill.md SHALL 定义以下核心工具及其 API 端点、请求/响应格式：
- 创建项目（`POST /api/v1/projects`）
- 获取/更新项目设置（`GET/PATCH /api/v1/projects/{name}`）
- Agent 对话（`POST /api/v1/agent/chat`）
- 项目列表（`GET /api/v1/projects`）
- 项目详情（`GET /api/v1/projects/{name}`）

#### Scenario: 工具定义完整性
- **WHEN** OpenClaw Agent 解析 skill.md 中的工具定义
- **THEN** 每个工具 SHALL 包含端点路径、HTTP 方法、请求参数说明、响应格式示例

### Requirement: 认证说明
skill.md SHALL 说明认证方式：用户从 ArcReel 设置页面获取 API Key（`arc-` 前缀），通过 `Authorization: Bearer <API_KEY>` 传递。

#### Scenario: 用户按说明配置认证
- **WHEN** 用户按 skill.md 中的认证说明操作
- **THEN** 能够成功调用所有 Skill 定义的 API 端点

### Requirement: OpenClaw 使用引导弹窗
前端项目大厅页顶栏 SHALL 提供 🦞 OpenClaw 按钮，点击弹出使用说明 Modal。

#### Scenario: 打开引导弹窗
- **WHEN** 用户点击顶栏的 🦞 OpenClaw 按钮
- **THEN** 弹出 Modal，包含：可复制的提示词（含动态 skill.md URL）、4 步使用说明、"获取 API 令牌"按钮

#### Scenario: 提示词中的 URL 动态适配
- **WHEN** 用户在 `http://localhost:1241` 访问并打开引导弹窗
- **THEN** 提示词中的 URL 为 `http://localhost:1241/skill.md`

#### Scenario: 获取 API 令牌
- **WHEN** 用户点击"获取 API 令牌"按钮
- **THEN** 跳转到 API Key 管理页面
