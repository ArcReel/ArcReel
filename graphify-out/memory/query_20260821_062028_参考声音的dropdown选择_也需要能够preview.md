---
type: "query"
date: "2026-08-21T06:20:28.015302+00:00"
question: "参考声音的Dropdown选择 也需要能够preview"
contributor: "graphify"
outcome: "useful"
source_nodes: ["AssetFormModal.tsx", "Asset"]
---

# Q: 参考声音的Dropdown选择 也需要能够preview

## Answer

Expanded from original query via graph vocab: [character, voice, audio, asset, dropdown, preview, selector]. The asset editing UI is implemented in AssetFormModal.tsx; the primary reference voice was a native select and Asset resources carry audio paths resolved by API.getGlobalAssetUrl. Replacing it with a custom audio resource picker provides per-resource preview controls while keeping selection explicit. Sources: frontend/src/components/assets/AssetFormModal.tsx and frontend/src/api.ts.

## Outcome

- Signal: useful

## Source Nodes

- AssetFormModal.tsx
- Asset