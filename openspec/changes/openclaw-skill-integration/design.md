## Context

ArcReel 当前使用 OAuth2 Bearer JWT 认证，所有 API 端点通过 `get_current_user` 依赖验证。前端先通过 `/auth/token` 获取 JWT，后续请求携带 `Authorization: Bearer <jwt>`。

OpenClaw 等外部平台需要长期有效的 API Key 来调用 ArcReel API，而非短期 JWT。此外，现有助手对话基于 SSE 流式，外部 Agent 需要同步请求-响应接口。

## Goals / Non-Goals

**Goals:**
- 在现有认证体系中增加 API Key 认证模式，与 JWT 认证共存
- 复用现有 API 端点，无需创建独立的"公开 API"层
- 提供同步 Agent 对话端点供外部调用
- 编写符合 OpenClaw AgentSkill 规范的 skill.md

**Non-Goals:**
- 不实现多用户/多租户体系（保持单用户模式）
- 不实现 API 调用频率限制（后续迭代）
- 不实现 API Key 权限范围控制（所有 key 拥有完整权限）
- 不重构现有 API 端点的路径或参数格式

## Decisions

### 1. API Key 格式与存储

**决定**: 使用 `arc-` 前缀 + 32 位随机字符串，数据库只存 SHA-256 哈希。

格式：`arc-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`（36 字符）

**理由**: 前缀便于用户识别来源，同时作为认证分流的判断依据；哈希存储确保数据库泄露不会暴露原始 key。参考 Zopia 的 `zopia-xxxxxxxxxxxx` 模式。

### 2. 认证层改造方式

**决定**: 修改 `server/auth.py` 的 `_verify_and_get_payload`，通过 `arc-` 前缀直接判定认证模式。

流程：
1. 提取 Bearer token
2. 检查 token 是否以 `arc-` 开头
3. **是 → API Key 路径**：计算 SHA-256 哈希 → 查数据库 → 成功则返回 `{"sub": "apikey:<key_name>", "via": "apikey"}`
4. **否 → JWT 路径**：JWT 解码验证 → 成功则返回 payload
5. 任一路径失败 → 返回 401

**理由**: 前缀判定是确定性的，避免不必要的 JWT 解码尝试。最小改动，所有现有端点自动获得 API Key 支持。

**替代方案**: 单独的认证中间件（改动更大）；新建独立路由前缀（违反复用原则）。

### 3. 同步 Agent 对话端点

**决定**: 新增 `POST /api/v1/agent/chat` 端点，内部创建临时会话 → 发送消息 → 收集 SSE 流直到完成 → 返回完整响应。

请求体：
```json
{
  "project_name": "my-project",
  "message": "帮我写一个悬疑剧本",
  "session_id": null  // 可选，传入则复用会话
}
```

响应体：
```json
{
  "session_id": "xxx",
  "reply": "好的，我来帮你...",
  "status": "completed"
}
```

**理由**: 参考 Zopia 的 `/api/v1/agent/chat` 设计，OpenClaw 等外部 Agent 不支持 SSE，需要同步接口。内部复用 AssistantService。

### 4. API Key 管理位置

**决定**: 后端新增 `server/routers/api_keys.py`，前端在设置页面新增 "API Keys" tab。

数据库模型 `ApiKey`：
- `id`: 主键
- `name`: 用户自定义名称
- `key_hash`: SHA-256 哈希
- `key_prefix`: 前 8 位（`arc-xxxx`）用于列表展示
- `created_at`: 创建时间
- `expires_at`: 过期时间（可选，默认 30 天）
- `last_used_at`: 最近使用时间

**理由**: 与现有 ORM 体系一致，复用 SQLAlchemy async + Alembic migration。

### 5. skill.md 动态服务

**决定**: skill.md 作为模板存放在 `public/skill.md.template`，其中 API URL 使用 `{{BASE_URL}}` 占位符。通过 FastAPI 路由 `GET /skill.md` 动态渲染：从请求的 `Host` header 和 scheme 推断实际 base URL，替换占位符后返回。

**理由**: 本项目是自部署服务，每个用户的域名/端口不同，skill.md 中的 API URL 必须动态适配。静态文件无法做到这一点。

**替代方案**: 让用户手动填写 base URL 配置（增加用户负担）；前端生成（OpenClaw 需要从服务端直接获取）。

### 6. 前端 OpenClaw 使用引导弹窗

**决定**: 在项目大厅页顶栏新增 🦞 OpenClaw 按钮，点击弹出使用说明 Modal，内容包括：
- 提示词（可复制）：`学习 https://<domain>/skill.md 然后遵循skill,任凭发挥创作视频`
- 使用步骤说明（4 步）
- "获取 API 令牌" 按钮（跳转到 API Key 管理页面）

弹窗中的提示词里的 URL 也需要动态替换为当前访问地址。

**理由**: 参考 Zopia 的引导设计，降低用户理解成本。

## Risks / Trade-offs

- **[性能]** API Key 每次请求需查库验证 → 加内存缓存（LRU, TTL 5 分钟），降低数据库压力
- **[安全]** API Key 长期有效 → 默认 30 天过期 + 支持手动吊销
- **[兼容性]** 同步对话端点可能超时 → 设置合理的响应超时（120 秒），超时后返回部分响应
- **[单用户]** API Key 不区分用户 → 当前单用户模式下可接受，多用户时需扩展
