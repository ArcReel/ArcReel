## Why

The storyboard scene panel (Timeline) has serious performance and UX issues in episodes with 30-100 storyboards: full DOM rendering causes images/videos to all load simultaneously and saturate bandwidth; any resource change triggers global cache invalidation causing all media to reload; when the Agent performs batch operations, notifications only show one of the changes, leaving the user with an incomplete picture.

## What Changes

- **Virtual scrolling**: TimelineCanvas introduces @tanstack/react-virtual, rendering only SegmentCards near the viewport, fundamentally reducing the number of concurrent requests
- **Lazy loading**: `<img>` gets `loading="lazy"`; `<video>` sets `src` only when entering the viewport
- **Precise cache invalidation**: Replace the global `mediaRevision: number` with `entityRevisions: Record<string, number>` (key is `entity_type:entity_id`), using `entity_type` + `entity_id` from SSE events to directly construct keys and only increment the version number of the changed entity. Covers all 7 consumers: SegmentCard, CharacterCard, ClueCard, OverviewCanvas, AssetSidebar, AvatarStack, VersionTimeMachine
- **Scroll-to-target adaptation**: `useScrollTarget` adapts to virtual scrolling, using `virtualizer.scrollToIndex()` instead of `scrollIntoView()`
- **Notification aggregation**: `useProjectEventsSSE` groups same-type changes in `onChanges` by `entity_type:action`; toast and workspace notifications display aggregated text (e.g., "AI added 3 characters: Zhang San, Li Si, Wang Wu")

## Capabilities

### New Capabilities
- `timeline-virtual-scroll`: Timeline virtual scrolling and media lazy loading, reducing concurrent network requests and DOM element count
- `precise-cache-invalidation`: Per-entity granularity cache invalidation mechanism replacing the global mediaRevision, covering all 7 media consumer components
- `batch-notification-aggregation`: SSE change notification aggregation, merging same-type changes in the same batch into one user-readable notification

### Modified Capabilities
(No existing spec-level behavior needs modification)

## Impact

- **Frontend dependencies**: Add `@tanstack/react-virtual`
- **Frontend components**: TimelineCanvas, SegmentCard (MediaColumn), CharacterCard, ClueCard, OverviewCanvas, AssetSidebar, AvatarStack, VersionTimeMachine, useScrollTarget hook
- **Frontend Store**: app-store (entityRevisions replaces mediaRevision)
- **Frontend Hooks**: useProjectEventsSSE (precise invalidation + notification aggregation), useProjectAssetSync (retains full invalidation as fallback)
- **Backend**: No changes (SSE events already contain sufficient information)
