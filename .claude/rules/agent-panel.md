---
paths:
  - "frontend/src/components/copilot/**"
  - "frontend/src/utils/entry-projection.ts"
---

# Agent 面板

- 消息区滚动交给 `components/ui/message-scroller`（贴底跟随、上翻停止、「跳到最新」）。发送消息、提交回答这类接下来要看回复的动作调用 `MessageFlowHandle.scrollToEnd()`；不手写 `scrollTop` 赋值或「内容变化就滚到底」的 effect。`MessageScrollerItem` 不用 `content-visibility: auto`，消息视口保持 `overflow-anchor: none`：Markdown 按需加载会改变消息高度，两者都会让跟随误判为用户上翻。
- 显示层的合并与过滤只在 `chat/display-items.ts` 的 `buildDisplayItems` 里做，并补单元测试。压缩摘要、子代理推断终态这类显示按 `utils/entry-projection` 的条目子类型投影，不按英文前缀嗅探文本。
- 工序（工具调用、子智能体、Skill、后台任务）统一用 `chat/WorkRow`。新增 ArcReel MCP 工具时同时登记 `tool_name_<id>` 和 `chat/arcreel-tool-summaries.ts` 的摘要格式，摘要里不写不透明 ID。
- 失败卡片只为查看期间新到达的失败播报 `role="alert"`，历史边界是 store 的 `historySeq`。
- 输入区：Agent 提问时由 `AgentQuestionnaire` 占用输入框的位置，输入框隐藏但保持挂载，不另开弹层；问卷高度上限 `70cqh`（有排队消息时 `50cqh`），只有题目区滚动。会话历史打开时替换消息区。
