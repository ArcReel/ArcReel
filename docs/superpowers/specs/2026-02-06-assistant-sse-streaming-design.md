# 设计文档：Assistant 会话 SSE 流式返回方案

## 1. 背景

当前助手会话链路已经具备：
- 会话与消息落库（Claude Agent SDK transcript 入库镜像，受 `ARCREEL_SDK_SESSION_STORE` 控制）
- Claude Agent SDK 接入
- 前端会话工作台（React）

> 演进说明：本设计为 SSE 流式的初版方案。落地后的接口形态有调整——见下文「实现差异」标注。
> 核心结论（采用 SSE、发送与订阅两步解耦、delta/tool/done/error 事件模型、partial messages 逐字输出）保持有效。

但消息返回仍是“收全后一次性返回”，导致：
- 首字延迟高，用户等待时间长
- 工具调用过程不可见（只能等最终结果）
- 前端无法实现逐字渲染与进度反馈

---

## 2. 目标与非目标

### 2.1 目标

- 使用 `SSE (text/event-stream)` 实现 assistant 响应流式返回
- 前端可增量渲染 token，并显示状态事件（开始、工具调用、完成、错误）
- 保持现有会话存储结构，最小化改动
- 保留同步接口作为降级路径

### 2.2 非目标

- 本期不引入 WebSocket
- 本期不重构现有 skill 执行逻辑
- 本期不引入复杂任务队列系统（如 Celery）

---

## 3. 方案选择

在“前后端分离 + 单向生成流”场景中，优先选 SSE：
- 协议简单，浏览器原生支持 `EventSource`
- 服务端改造成本低，适配 FastAPI 容易
- 语义匹配“用户发一次请求，服务端持续输出”

权衡：
- SSE 单向，不适合复杂双向控制（后续如需“实时中断+交互工具输入”再评估 WebSocket）

---

## 4. 总体架构

```text
POST /sessions/send                GET /sessions/{session_id}/stream
前端 -----------> 后端落 user 消息 -----------> 前端 EventSource 订阅
                          |                            |
                          |---- Claude SDK async ---->|
                          |---- tool/status events --->|
                          |---- delta token events --->|
                          |---- done/error ----------->|
                          |---- 落 assistant 消息 ------|
```

设计原则：
- 发送消息与消费流解耦（两步）
- 先落 user 消息，再开流
- assistant 最终消息在 `done` 时一次落库（可选扩展 chunk 落库）

---

## 5. API 设计

> 实现差异：落地接口为 `POST /api/v1/.../assistant/sessions/send`（发送消息）+
> `GET /api/v1/.../assistant/sessions/{session_id}/stream`（按会话订阅 SSE）。未采用下文的
> per-request `request_id` / `stream_url` 形态，改为按 `session_id` 订阅会话事件流。下文请求/响应
> 体仅示意字段语义。

## 5.1 发送消息（保持 POST 语义）

`POST /api/v1/projects/{project_name}/assistant/sessions/send`

请求体：
```json
{
  "content": "帮我生成第一集角色设定",
  "images": [],
  "session_id": "xxx-optional"
}
```

响应：
```json
{
  "status": "accepted",
  "session_id": "xxx"
}
```

说明：
- `session_id` 省略时创建新会话并返回新建的 `session_id`；传入时向既有会话追加消息
- 发送只负责入队（`status: accepted`），不直接返回 assistant 文本；流式内容统一由下文 stream 端点订阅
- `images` 为可选附件（最多 5 张）

## 5.2 订阅流

`GET /api/v1/assistant/sessions/{session_id}/stream`（按会话订阅）

响应头：
- `Content-Type: text/event-stream`
- `Cache-Control: no-cache`
- `Connection: keep-alive`
- `X-Accel-Buffering: no`（如有反向代理）

事件格式（SSE）：
```text
id: 1
event: ack
data: {"session_id":"xxx"}

id: 2
event: delta
data: {"text":"你好，"}

id: 3
event: tool_call
data: {"name":"Skill","detail":"generate-script"}

id: 4
event: tool_result
data: {"ok":true,"summary":"已生成 episode_1.json"}

id: 5
event: done
data: {"assistant_message_id":102,"usage":{"tokens":1234}}
```

错误事件：
```text
event: error
data: {"code":"SDK_ERROR","message":"..."}
```

心跳事件（每 15-30 秒）：
```text
event: ping
data: {"ts":"2026-02-06T16:00:00Z"}
```

---

## 6. 事件模型与状态机

推荐事件类型：
- `ack`：流已建立
- `delta`：文本增量
- `tool_call`：工具调用开始
- `tool_result`：工具调用结果摘要
- `meta`：模型/技能信息（可选）
- `ping`：保活心跳
- `done`：完成并落库
- `error`：失败并结束

状态机：
- `created -> streaming -> done`
- `created -> streaming -> error`
- `created -> cancelled`（客户端断开可选）

---

## 7. 后端实现方案（FastAPI）

## 7.1 文件改动（最小化）

> 实现差异：agent_runtime 位于 `server/agent_runtime/`；SSE 事件构建落在
> `stream_projector.py`（StreamProjector）+ `session_manager.py`（订阅者模式）+
> `session_actor.py`（每会话串行化 SDK 调用），未单独建 `streaming.py`。

新增/修改（概念位置）：
- `server/agent_runtime/stream_projector.py`
  - 从流式事件构建实时助手回复，事件序列化
- `server/agent_runtime/service.py`
  - 提供 `stream_events(...)`（async generator）
  - 将 Claude SDK 异步消息映射为 SSE 事件
- `server/routers/assistant.py`
  - `POST /sessions/send` 发送消息
  - `GET /sessions/{session_id}/stream` 订阅 SSE

## 7.2 关键实现点

1. 生产者-消费者模型  
- 生产者：拉取 Claude SDK `query()` 异步消息
- 消费者：SSE generator 持续 `yield` 事件
- Claude SDK 需启用 `include_partial_messages=true`，并解析 `stream_event` 的 `text_delta` 才能实现真正逐步输出

2. 断连处理  
- 检测 `request.is_disconnected()`
- 客户端断开后取消 SDK 任务并释放 request 资源

3. 落库策略  
- 进入流前先落 user 消息
- 结束时（`done`）落 assistant 消息
- 异常时可落一条错误摘要消息（便于排障）

4. 顺序保证  
- 每个事件带单调递增 `id`
- `done/error` 必须是终止事件

5. 降级策略  
- 当 `stream=false` 或客户端不支持 SSE，沿用原同步接口

---

## 8. 前端实现方案（React + Streamdown）

## 8.1 发送流程

1. 先 `POST /sessions/send` 入队消息，拿到 `session_id`
2. 用 `new EventSource("/sessions/{session_id}/stream")` 订阅会话事件流
3. 处理事件并实时渲染
4. 收到 `done/error` 后关闭连接，刷新一次消息历史

## 8.2 UI 渲染策略

- `ack`：显示“正在生成...”
- `delta`：追加到当前 assistant 气泡，并交给 Streamdown 增量渲染 Markdown
- `tool_call/tool_result`：在气泡下显示状态行
- `done`：标记完成态，解除输入框禁用
- `error`：显示错误并允许重试

Streamdown 集成建议：
- assistant 消息统一通过 `Streamdown` 组件渲染
- 开启不完整 Markdown 解析（`parseIncompleteMarkdown`），避免流式阶段代码块/列表闪烁
- 若 CDN 组件加载失败，前端降级为纯文本渲染，保证可用性

## 8.3 与 Slash 提示兼容

- `/` 提示逻辑不变
- 仅替换“发送后回包处理”部分为流式消费

---

## 9. 安全与网关兼容

1. 鉴权  
- 若后续需要 Header 鉴权，原生 `EventSource` 不支持自定义 Header  
- 可选方案：
  - 短期：stream 端点 URL 携带一次性短时 token
  - 中期：改为 `fetch + event-stream parser`（仍是 SSE 协议）

2. 反向代理  
- Nginx/网关禁用缓冲（否则“伪流式”）
- 适当提高超时配置（读超时、keepalive）

3. CORS  
- 为 SSE 路由保持与 API 一致的跨域策略

---

## 10. 监控与可观测性

建议埋点：
- `assistant_stream_requests_total`
- `assistant_stream_time_to_first_delta_ms`
- `assistant_stream_duration_ms`
- `assistant_stream_error_total`
- `assistant_stream_disconnect_total`

日志关键字段：
- `session_id`, `project_name`
- `event_count`, `delta_chars`, `tool_calls`
- `error_code`, `error_message`

---

## 11. 分阶段实施计划

## Phase 1：协议与后端最小闭环
- [ ] `POST /sessions/send` 入队消息并返回 `session_id`
- [ ] 新增 `GET /sessions/{session_id}/stream` 输出 `ack/delta/done/error`
- [ ] assistant 文本在 `done` 时落库

验收：
- 浏览器可看到逐步输出文本
- 数据库中 user/assistant 消息完整

## Phase 2：工具事件与 UI 状态增强
- [ ] 增加 `tool_call/tool_result/ping`
- [ ] 前端显示“调用技能中/完成”
- [ ] 错误态可重试

验收：
- 用户能感知“模型思考 + 工具执行”过程

## Phase 3：稳定性与恢复
- [ ] 断连清理任务
- [ ] 发送幂等（重试去重）
- [ ] 指标与日志完善

验收：
- 高频发送、弱网断连下无明显资源泄漏和重复消息

---

## 12. 验收标准（DoD）

- [ ] 首字延迟显著下降（可观测）
- [ ] 流式渲染全过程稳定，无卡死
- [ ] `done/error` 一定闭环，前端状态可恢复
- [ ] 消息落库与历史回放一致
- [ ] 同步接口保留，开关可回退

---

## 13. 风险与规避

1. 代理缓冲导致非实时  
规避：显式关闭代理缓冲，增加心跳

2. 客户端断开后服务端任务悬挂  
规避：检测断连并取消上游任务，设置超时

3. 流式输出与落库不一致  
规避：以 `done` 为落库提交点；异常写错误摘要

4. EventSource 鉴权限制  
规避：短期 token；长期可切 `fetch + SSE parser`

---

## 14. 建议首批改动清单（文件级）

- `server/agent_runtime/service.py`：新增流式接口与事件映射
- `server/agent_runtime/stream_projector.py`：SSE 事件构建与序列化
- `server/routers/assistant.py`：发送与 stream 路由
- 前端助手 API 客户端：新增 stream 相关 API
- 前端助手会话组件：接入 EventSource 增量渲染 + Streamdown
- `tests/`：新增流式接口与事件序列单测
