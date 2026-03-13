## 1. 后端 API 层

- [x] 1.1 在 `server/routers/assistant.py` 新增 `ImageAttachment` Pydantic 模型（`data: str`、`media_type: str`）
- [x] 1.2 在 `SendMessageRequest` 新增 `images: list[ImageAttachment] = Field(default_factory=list, max_length=5)`
- [x] 1.3 路由 `send_message` 将 `req.images` 透传给 `service.send_message()`

## 2. 后端 Service 层

- [x] 2.1 在 `server/agent_runtime/service.py` 的 `send_message` 签名新增 `images` 参数（默认为 `None`）
- [x] 2.2 实现 `_build_multimodal_prompt(text, images)` async generator，构造含 text + image blocks 的 SDK message dict
- [x] 2.3 有图片时调用 `_build_multimodal_prompt` 得到 async generator，无图片时仍传 str

## 3. 后端 SessionManager 层

- [x] 3.1 在 `session_manager.py` 的 `send_message` 签名中将 `content: str` 改为 `prompt: str | AsyncIterable[dict]`，新增 `echo_text: str | None = None` 参数
- [x] 3.2 echo 逻辑改用 `echo_text or (prompt if isinstance(prompt, str) else "")` 作为气泡显示文本
- [x] 3.3 `_build_user_echo_message` 支持传入 content blocks 列表（含图片 blocks），以便即时气泡也能显示图片

## 4. 后端规范化层

- [x] 4.1 在 `server/agent_runtime/turn_schema.py` 的 `normalize_block` 中显式添加 `elif block_type == "image": pass` 分支，表明 image block 有意透传

## 5. 前端类型

- [x] 5.1 在 `frontend/src/types/assistant.ts` 的 `ContentBlock` type union 中新增 `"image"`
- [x] 5.2 在 `ContentBlock` 接口新增 `source?: { type: "base64"; media_type: string; data: string }` 字段

## 6. 前端渲染

- [x] 6.1 在 `ContentBlockRenderer.tsx` 新增 `case "image"` 分支，渲染 `<img src="data:..." className="max-w-full max-h-64 rounded-lg mt-1" />`

## 7. 前端 Hook

- [x] 7.1 在 `useAssistantSession` 的 `sendMessage` 签名中新增 `images?: AttachedImage[]` 参数
- [x] 7.2 组装请求体：将 `images` 映射为 `{ data: dataUrl.split(",")[1], media_type: mimeType }` 数组

## 8. 前端 AgentCopilot UI

- [x] 8.1 定义 `AttachedImage` 接口（`id`, `dataUrl`, `mimeType`），新增 `attachedImages` state
- [x] 8.2 实现 `handlePaste`：从 `ClipboardEvent` 读取 `image/*` items，转 base64 加入附件列表
- [x] 8.3 实现 `handleDrop` + `handleDragOver`：从 `DataTransfer.files` 读取图片，含拖入高亮效果
- [x] 8.4 实现 `handleFileSelect`：隐藏 `<input type="file" multiple accept="image/*">` 的 onChange
- [x] 8.5 输入框旁添加📎按钮（触发 file input），绑定 `onPaste`、`onDrop`、`onDragOver` 到输入区
- [x] 8.6 实现缩略图条：`attachedImages` 非空时在 textarea 上方渲染 64×64 缩略图 + 右上角 × 移除按钮
- [x] 8.7 超出 5 张时禁用附件按钮；单张 > 5MB 时显示错误提示并拒绝添加
- [x] 8.8 `handleSend` 调用 `sendMessage(text, attachedImages)`，发送后执行 `setAttachedImages([])`
