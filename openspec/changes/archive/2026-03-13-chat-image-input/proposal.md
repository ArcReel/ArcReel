## Why

The conversation panel currently only supports plain text input; users cannot directly pass images to the AI Agent for analysis (e.g., reference images, screenshots, storyboard sketches, etc.). To allow the Agent to assist in creation based on visual content, image input support is needed in the conversation panel.

## What Changes

- New image attachment functionality in the conversation input area: supports three methods — click to upload, drag-and-drop, and paste (Ctrl+V)
- Display thumbnail previews of images pending sending in the input area, with support for individual removal
- When sending, images are submitted to the backend along with the text content; the backend passes them to the Agent (specific passing method to be confirmed via brainstorm)
- Backend API extended to support messages carrying image data

## Capabilities

### New Capabilities

- `chat-image-attachment`: Conversation panel image attachment — frontend UI collection, preview, and removal of images; combining with text on send; backend receiving and passing to Agent

### Modified Capabilities

- `sync-agent-chat`: Message sending interface needs to support carrying image data (specific format TBD)

## Impact

- **Frontend**: `AgentCopilot.tsx` input area interaction, `useAssistantSession` hook's `sendMessage` signature
- **Backend API**: `server/routers/assistant.py` — `SendMessageRequest` extends image fields
- **Backend services**: `server/agent_runtime/service.py`, `session_manager.py` — message construction logic
- **Dependencies**: Browser native File API; Claude SDK side approach pending evaluation
