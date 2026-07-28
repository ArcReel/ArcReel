## Context

AgentCopilot 对话框当前只支持纯文本输入。Claude Agent SDK 的 `ClaudeSDKClient.query()` 签名为 `str | AsyncIterable[dict]`，AsyncIterable 路径支持传递完整的 multimodal content（文字 + 图片 blocks）。后端 `send_message` 目前只走 `str` 路径，Service / SessionManager / Router 三层均不感知图片。

## Goals / Non-Goals

**Goals:**
- 用户可通过粘贴（Ctrl+V）、点击上传、拖拽三种方式在对话框附加图片
- 图片以 base64 inline 方式随消息一并发送给 Agent
- 发送后的气泡及历史回放均能正确渲染图片

**Non-Goals:**
- 图片 URL 引用（需后端额外抓取，不在本期）
- 服务端图片压缩或格式转换
- 图片全屏预览弹窗

## Decisions

### 决策 1：图片传输方式 — Base64 inline in JSON

**选择**：图片 base64 直接内嵌在 `SendMessageRequest.images[]` 中，一次请求发送。

**备选**：先 POST multipart 上传得到临时 ID，再在消息中引用。

**原因**：无需新增上传接口，前后端改动最小；单次请求无状态，不需要临时文件生命周期管理。图片上限 5 张 × 5MB，JSON body 最大 ~33MB，可接受。

---

### 决策 2：SDK 集成层 — Service 层封装

**选择**：`AssistantService.send_message()` 负责把 `content + images` 组装为 AsyncGenerator，传给 SessionManager；SessionManager 只感知 `str | AsyncIterable[dict]`，不理解图片结构。

**备选 A**：Router 层序列化（最外层处理）。
**备选 B**：SessionManager 内部处理（最底层）。

**原因**：Service 是业务逻辑层，Router 做 HTTP 边界校验，SessionManager 做 SDK 通信管理，职责分层清晰。Service 集中处理内容格式化，测试和后续扩展更方便。

---

### 决策 3：echo_text 与 sdk_prompt 分离

**选择**：`SessionManager.send_message()` 新增 `echo_text` 参数，用于用户气泡展示；`prompt` 参数传入 sdk_prompt（可为 str 或 AsyncGenerator）。

**原因**：用户气泡只需展示文字部分；AsyncGenerator 不可重复消费，不能既用于 SDK 又用于 echo 构建。分离两者避免耦合。

## Risks / Trade-offs

- **JSON body 体积**：5 张 5MB 图片 base64 后约 33MB。需在前端做文件大小校验（≤ 5MB/张），避免 413 错误。建议同时在后端 FastAPI 配置 `max_body_size`。
  → 缓解：前端拦截 + 后端限制双保险。

- **echo message 含图片 base64**：`_build_user_echo_message` 在 message_buffer 中存储完整 base64，内存占用上升。
  → 缓解：buffer 本身有 prune 机制，且 echo 消息在 transcript 确认后会被去重清除。

- **normalize_block 对 image block 的透传**：`turn_schema.normalize_block` 对未知 type 做 deepcopy 透传，不会丢失数据；但未来若加类型白名单需注意同步添加 `"image"`。

## Migration Plan

纯新增，无数据迁移。前后端独立部署均向后兼容：
- 旧前端不发 `images` 字段 → 后端 `images` 默认空列表，走原有路径
- 新前端发 `images` → 需新后端支持，需同步部署

## Open Questions

- 是否需要在 `normalize_block` 中显式注册 `"image"` type（加 `elif block_type == "image": pass`），还是依赖现有 deepcopy 透传即可？建议显式注册以提高可读性，但不影响功能。
