---
type: "query"
date: "2026-08-21T10:53:48.073811+00:00"
question: "然后右下角的这个提示的这么一个progress bar，它应该是有一个可以缩小的一个状态，不然它可能会挡到这个agents的一个输入的这个页面。"
contributor: "graphify"
outcome: "useful"
source_nodes: ["CharacterCatalogSyncMonitor", "CharacterCatalogSyncMonitor.test.tsx", "AgentCopilot.tsx", "UI_LAYERS", "character-catalog-sync-store.ts"]
---

# Q: 然后右下角的这个提示的这么一个progress bar，它应该是有一个可以缩小的一个状态，不然它可能会挡到这个agents的一个输入的这个页面。

## Answer

Expanded from the original query via graph vocabulary: [progress, monitor, toast, bottom, agent, input, overlay, collapse, expand, sync, job]. CharacterCatalogSyncMonitor is a global z-60 fixed bottom-right card, while AgentCopilot occupies the right-side workspace and places its composer at the bottom, so the 18rem-wide monitor can overlap the input. The agreed design should add an explicit minimize control, a compact 64x40 progress pill with spinner and percentage, an expand control, preserve the minimized state for the same job across route navigation, reset expanded for a new job, continue polling while minimized, and disappear normally on terminal status. This should be implemented together with the UTC timestamp normalization and terminal-state precedence fix that stops the stale running state.

## Outcome

- Signal: useful

## Source Nodes

- CharacterCatalogSyncMonitor
- CharacterCatalogSyncMonitor.test.tsx
- AgentCopilot.tsx
- UI_LAYERS
- character-catalog-sync-store.ts