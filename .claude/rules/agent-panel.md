---
paths:
  - "frontend/src/components/copilot/**"
  - "frontend/src/utils/entry-projection.ts"
---

# Agent panel

- Delegate message scrolling to `components/ui/message-scroller` (follow the bottom, stop following when the user scrolls up, and jump to latest). Actions after which the user expects a reply, such as sending a message or submitting an answer, call `MessageFlowHandle.scrollToEnd()`. Do not assign `scrollTop` manually or add effects that scroll to the bottom whenever content changes. Do not use `content-visibility: auto` on `MessageScrollerItem`; keep `overflow-anchor: none` on the message viewport. Lazy-loaded Markdown changes message height, and either mechanism can make following logic mistake this for the user scrolling up.
- Merge and filter display items only in `buildDisplayItems` in `chat/display-items.ts`, with unit tests. Project displays such as compaction summaries and inferred subagent terminal states from entry subtypes in `utils/entry-projection`; do not sniff English text prefixes.
- Use `chat/WorkRow` for all work items (tool calls, subagents, Skills, background tasks). When adding an ArcReel MCP tool, also register `tool_name_<id>` and its summary format in `chat/arcreel-tool-summaries.ts`; summaries must not contain opaque IDs.
- Failure cards announce `role="alert"` only for failures arriving while the view is open. The history boundary is the store's `historySeq`.
- Input area: when the Agent asks questions, `AgentQuestionnaire` occupies the input's position. Hide the input while keeping it mounted; do not open a separate overlay. Limit questionnaire height to `70cqh` (`50cqh` when messages are queued), with only the questions area scrolling. Session history replaces the messages area when opened.
