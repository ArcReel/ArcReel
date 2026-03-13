## Why

对话框目前只支持纯文本输入，用户无法直接传递图片给 AI Agent 进行分析（如参考图、截图、分镜草图等）。为了让 Agent 能基于视觉内容辅助创作，需要在对话框中支持图片输入。

## What Changes

- 对话框输入区新增图片附件功能：支持点击上传、拖拽放入、粘贴（Ctrl+V）三种方式
- 输入区展示待发送图片的缩略图预览，支持逐张移除
- 发送时图片随文字一并传给后端，后端转交给 Agent（具体传递方式待 brainstorm 确认）
- 后端 API 扩展以支持携带图片数据的消息

## Capabilities

### New Capabilities

- `chat-image-attachment`: 对话框图片附件——前端 UI 收集、预览、移除图片；发送时与文字合并；后端接收并传递给 Agent

### Modified Capabilities

- `sync-agent-chat`: 消息发送接口需要支持附带图片数据（具体格式待定）

## Impact

- **前端**：`AgentCopilot.tsx` 输入区交互、`useAssistantSession` hook 的 `sendMessage` 签名
- **后端 API**：`server/routers/assistant.py` — `SendMessageRequest` 扩展图片字段
- **后端服务**：`server/agent_runtime/service.py`、`session_manager.py` — 消息构造逻辑
- **依赖**：浏览器原生 File API；Claude SDK 侧方案待评估
