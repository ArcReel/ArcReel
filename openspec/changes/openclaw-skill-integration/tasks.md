## 1. 数据库层 — ApiKey 模型与迁移

- [x] 1.1 创建 `lib/db/models/api_key.py` ORM 模型（id, name, key_hash, key_prefix, created_at, expires_at, last_used_at）
- [x] 1.2 在 `lib/db/models/__init__.py` 中注册新模型
- [x] 1.3 生成 Alembic migration 并执行 `alembic upgrade head`
- [x] 1.4 创建 `lib/db/repositories/api_key_repository.py`（CRUD + 按哈希查询 + 更新 last_used_at）

## 2. 认证层改造 — API Key 认证分流

- [x] 2.1 修改 `server/auth.py` 的 `_verify_and_get_payload`，以 `arc-` 前缀判定走 API Key 或 JWT 路径
- [x] 2.2 实现 API Key 验证逻辑（SHA-256 哈希 → 查库 → 检查过期 → 返回 payload）
- [x] 2.3 添加 API Key 查询结果的 LRU 内存缓存（TTL 5 分钟）
- [x] 2.4 编写认证分流的单元测试（API Key 成功/过期/不存在、JWT 不受影响）

## 3. API Key 管理路由

- [x] 3.1 创建 `server/routers/api_keys.py`（POST 创建、GET 列表、DELETE 删除）
- [x] 3.2 实现 API Key 生成逻辑（`arc-` + 32 位随机字符串，哈希存储，创建时返回完整 key）
- [x] 3.3 在 `server/app.py` 中注册路由
- [x] 3.4 删除 key 时清除缓存
- [x] 3.5 编写 API Key 管理端点的集成测试

## 4. 同步 Agent 对话端点

- [x] 4.1 创建 `POST /api/v1/agent/chat` 端点（新建或复用会话 → 发消息 → 收集完整响应）
- [x] 4.2 内部对接 AssistantService，收集 SSE 事件流直到完成
- [x] 4.3 实现 120 秒超时处理，超时返回部分响应 + status: "timeout"
- [x] 4.4 编写同步对话端点的测试

## 5. Skill 定义文件与动态渲染

- [x] 5.1 创建 `public/skill.md.template`，参考 Zopia 格式编写 ArcReel Skill 定义，API URL 使用 `{{BASE_URL}}` 占位符
- [x] 5.2 创建 `GET /skill.md` 路由（无需认证），从请求 Host/scheme 推断 base URL，替换占位符后返回
- [x] 5.3 验证不同部署地址下 `GET /skill.md` 返回正确的动态 URL

## 6. 前端 — API Key 管理页面

- [x] 6.1 在设置页面新增 "API Keys" tab 组件
- [x] 6.2 实现 API Key 列表展示（名称、前缀、创建时间、过期时间、最近使用）
- [x] 6.3 实现创建 API Key 功能（弹窗显示完整 key，提示仅此一次可见）
- [x] 6.4 实现删除 API Key 功能（确认弹窗）

## 7. 前端 — OpenClaw 引导弹窗

- [x] 7.1 在项目大厅页顶栏添加 🦞 OpenClaw 按钮
- [x] 7.2 实现引导 Modal 组件：提示词区域（可复制，含动态 skill.md URL）、4 步使用说明、"获取 API 令牌"按钮
- [x] 7.3 提示词中的 URL 动态适配当前访问地址（`window.location.origin`）
- [x] 7.4 "获取 API 令牌"按钮跳转到 API Key 管理页面
