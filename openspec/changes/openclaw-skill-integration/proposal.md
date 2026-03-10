## Why

OpenClaw 是 2026 年最热门的开源 AI Agent 平台（GitHub 247k+ stars），支持通过 AgentSkill 扩展能力。为 ArcReel 接入 OpenClaw Skill，可让用户通过自然语言对话调用 ArcReel 的项目创建、剧本生成、分镜制作、视频生成等能力，降低使用门槛并拓展获客渠道。

## What Changes

- 新增 API Key 认证模式：在现有 OAuth2 认证基础上，增加 `Authorization: Bearer <API_KEY>` 认证方式，复用现有 API 端点
- 新增 API Key 管理功能：前端提供 Token 生成页面，后端提供 CRUD 接口
- 新增同步 Agent 对话端点：现有助手 API 基于 SSE 流式，需提供一个同步请求-响应接口供 OpenClaw 调用
- 编写 OpenClaw AgentSkill 定义文件（`skill.md`），参考 Zopia 格式描述可用工具与调用方式

## Capabilities

### New Capabilities

- `api-key-auth`: API Key 生成、管理与 Bearer Token 认证机制，作为现有认证系统的补充认证模式
- `sync-agent-chat`: 同步 Agent 对话端点，封装现有 SSE 流式助手为请求-响应模式
- `openclaw-skill-def`: OpenClaw AgentSkill 规范的 Skill 定义文件，描述工作流与可用 API

### Modified Capabilities

（无需修改现有 capability 的需求定义，仅在认证中间件层兼容新的 API Key 模式）

## Impact

- **认证层**：`server/routers/auth.py` 中的 `get_current_user` 需兼容 API Key 认证
- **数据库**：新增 `ApiKey` ORM 模型与 migration
- **后端路由**：新增 API Key 管理路由、同步 Agent 对话路由
- **前端**：新增 API Key 管理页面（设置区域）
- **项目根目录**：新增 `skill.md` 定义文件供 OpenClaw 读取
