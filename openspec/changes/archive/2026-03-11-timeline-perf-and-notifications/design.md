## Context

ArcReel's frontend storyboard panel (TimelineCanvas) uses a simple `overflow-y-auto` vertical stack for all SegmentCards, with no virtual scrolling or lazy loading. An episode typically has 30-100 storyboards; when the page loads all images and video requests are initiated simultaneously.

When any resource changes, `invalidateMediaAssets()` increments the global `mediaRevision` counter, causing all components subscribed to this value (SegmentCard, CharacterCard, ClueCard, OverviewCanvas, AssetSidebar, AvatarStack, VersionTimeMachine) to simultaneously trigger media URL `?v=N` changes, making the browser reload all resources.

When the Agent performs batch operations (e.g., adding 5 characters at once), the backend diff correctly generates multiple change events, but the frontend's `selectPrimaryChange()` / `selectNotificationChange()` only selects 1 of the array entries for display.

## Goals / Non-Goals

**Goals:**
- Reduce Timeline concurrent media requests from N (total storyboards) to the viewport-visible count + overscan (approximately 8-12)
- When a single entity changes, other entities' media resources are not reloaded (covering all 7 consumer components)
- When the Agent performs batch operations, users can perceive all changes (aggregated notifications)

**Non-Goals:**
- Backend SSE protocol changes (current event information is already sufficient)
- Server-side cache headers (ETag/Last-Modified) optimization
- Pagination / infinite scroll
- Notification center / notification history

## Decisions

### 1. Virtual Scrolling Library: @tanstack/react-virtual

**Choice**: @tanstack/react-virtual v3
**Alternatives**: react-window, react-virtuoso
**Rationale**:
- Natively supports dynamic heights (`measureElement` + ResizeObserver); SegmentCards have expand/collapse states
- No UI invasiveness; only provides a virtualizer hook; compatible with the existing Tailwind styling system
- The project already uses the @tanstack ecosystem (react-query, etc.); maintains tech stack consistency
- `estimateSize` set to 200px estimated height; overscan set to 5

### 2. Lazy Loading Strategy: Native + Virtual Scrolling

**Choice**: Virtual scrolling naturally achieves lazy loading (SegmentCards outside the viewport are not rendered); viewport `<img>` tags get an additional `loading="lazy"` as a secondary safety net
**Rationale**: Virtual scrolling already fundamentally solves the problem; `loading="lazy"` is only a supplementary optimization for images in the overscan area, requiring no additional IntersectionObserver logic

### 3. Cache Invalidation: Per-Entity Version Numbers (key is entity_type:entity_id)

**Choice**: `entityRevisions: Record<string, number>`, key format is `entity_type:entity_id` (e.g., `segment:seg_001`, `character:Zhang San`, `clue:Weapon`, `project:project`)
**Alternatives**:
- Per-file-path version numbers — file paths for characters/clues are uncertain, requiring extra derivation logic
- Frontend diffing of before/after scripts data — high complexity, unreliable
**Rationale**:
- SSE events have `entity_type` + `entity_id` directly available, requiring no derivation
- Uniformly covers all 7 consumer components; each component subscribes per its own entity key
- The Worker's batch send path (`emit_project_change_batch`) correctly handles both first-time generation and regeneration
- Retain `invalidateAllEntities()` as a fallback (when there is no specific key information at task polling channel completion)

**Consumer migration list:**

| Component | Original subscription | New subscription key |
|-----------|----------------------|---------------------|
| SegmentCard | `mediaRevision` | `segment:{segment_id}` |
| CharacterCard | `mediaRevision` | `character:{character_name}` |
| ClueCard | `mediaRevision` | `clue:{clue_name}` |
| OverviewCanvas | `mediaRevision` | `project:project` |
| AssetSidebar | `mediaRevision` (props passed through) | `character:{name}` / `clue:{name}` (each sub-component subscribes independently) |
| AvatarStack | `mediaRevision` | `character:{name}` / `clue:{name}` |
| VersionTimeMachine | `mediaRevision` | `{resourceType}:{resourceId}` (dynamic key) |

### 4. Scroll-to-Target Adaptation

**Choice**: Maintain a `segmentId → virtualIndex` mapping; when scrollTarget is triggered, call `virtualizer.scrollToIndex()`
**Rationale**: In virtual scrolling, the target segment may not be in the DOM; `getElementById` + `scrollIntoView()` cannot be used

### 5. Notification Aggregation: Group by entity_type:action

**Choice**: Group same-batch changes by `entity_type:action`; generate one aggregated text per group
**Alternatives**: Toast one notification per change
**Rationale**: Toasting 5 notifications for 5 characters added in batch is a poor experience; aggregating to "AI added 3 characters: Zhang San, Li Si, Wang Wu" is more user-friendly

## Risks / Trade-offs

- **[Dynamic height estimation error]** → Inaccurate `estimateSize` may cause scroll jumping; mitigated by real-time correction via `measureElement`
- **[entityRevisions memory growth]** → Larger projects may have more keys; in practice, 100 storyboards + 20 characters + 10 clues ≈ 130 keys; negligible
- **[Full invalidation fallback path experience]** → `useProjectAssetSync` still triggers full invalidation when a task completes; this is a rare situation (compensation when SSE disconnects); acceptable
- **[AssetSidebar props refactoring]** → Current AssetSidebar receives `mediaRevision` via props; needs to be changed to sub-components each subscribing to the store directly; slightly larger change but more consistent with zustand best practices
- **[Aggregated notification text truncation]** → When there are more than 5 same-type changes, truncate to "AI added 5 characters: Zhang San, Li Si... etc."
