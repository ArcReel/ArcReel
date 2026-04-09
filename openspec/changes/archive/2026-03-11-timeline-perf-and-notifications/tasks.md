## 1. Frontend Dependencies and Infrastructure

- [x] 1.1 Install @tanstack/react-virtual dependency
- [x] 1.2 Implement `entityRevisions: Record<string, number>` in app-store to replace `mediaRevision: number`, providing `invalidateEntities(keys: string[])` and `getEntityRevision(key: string)` methods, retaining `invalidateAllEntities()` as a fallback for full invalidation

## 2. Virtual Scrolling

- [x] 2.1 Introduce `useVirtualizer` in TimelineCanvas, configure `estimateSize`(200px), `overscan`(5), and `measureElement`
- [x] 2.2 Change the SegmentCard list from `segments.map()` full rendering to `virtualItems.map()` absolute positioning rendering
- [x] 2.3 Adapt `useScrollTarget`: maintain a `segmentId → virtualIndex` mapping; when scrollTarget is triggered, call `virtualizer.scrollToIndex()`; execute flash highlight after scrolling completes

## 3. Migrate All Consumers to Precise Subscription

- [x] 3.1 SegmentCard: change `mediaRevision` subscription to `entityRevisions["segment:{segment_id}"]`; add `loading="lazy"` to `<img>`
- [x] 3.2 CharacterCard: change `mediaRevision` subscription to `entityRevisions["character:{name}"]`
- [x] 3.3 ClueCard: change `mediaRevision` subscription to `entityRevisions["clue:{name}"]`
- [x] 3.4 OverviewCanvas: change `mediaRevision` subscription to `entityRevisions["project:project"]`
- [x] 3.5 AssetSidebar: remove `mediaRevision` props passing; change each sub-component (CharacterSheetCard, ClueSheetCard) to directly subscribe to the corresponding entity key in the store
- [x] 3.6 AvatarStack: change `mediaRevision` subscription to subscribe per avatar by `character:{name}` / `clue:{name}`
- [x] 3.7 VersionTimeMachine: change `mediaRevision` subscription to `entityRevisions["{resourceType}:{resourceId}"]`

## 4. Precise Cache Invalidation

- [x] 4.1 Modify the `onChanges` callback in useProjectEventsSSE: construct a key from the `entity_type` + `entity_id` of SSE change events; call `invalidateEntities(keys)` to replace `invalidateMediaAssets()`
- [x] 4.2 Modify `refreshProject` in useProjectEventsSSE: no longer call `invalidateMediaAssets()` (precise invalidation is already handled in onChanges)
- [x] 4.3 Modify useProjectAssetSync: call `invalidateAllEntities()` as a fallback when a task completes
- [x] 4.4 Clean up the deprecated `mediaRevision` field and `invalidateMediaAssets()` method from app-store (after confirming no other consumers)

## 5. Notification Aggregation

- [x] 5.1 Implement the `groupChangesByType(changes)` utility function: group changes by `entity_type:action`
- [x] 5.2 Implement `formatGroupedNotificationText(group)` and `formatGroupedDeferredText(group)` aggregated text functions, supporting truncation (show "...etc." when there are more than 5)
- [x] 5.3 Modify the `onChanges` callback in useProjectEventsSSE: replace `selectNotificationChange` → one toast per group after grouping; replace `selectPrimaryChange` → one workspace notification per group after grouping (navigate to the first in the group)

## 6. Testing and Validation

- [x] 6.1 Write unit tests for notification aggregation functions (groupChangesByType, formatGroupedNotificationText)
- [x] 6.2 Write unit tests for precise cache invalidation logic (entityRevisions's invalidateEntities / invalidateAllEntities)
- [x] 6.3 Update references to `mediaRevision` in existing tests (stores.test.ts, useProjectAssetSync.test.tsx, OverviewCanvas.test.tsx)
- [x] 6.4 Run full frontend tests (`pnpm check`) to confirm no regressions
- [x] 6.5 Run full backend tests (`pytest`) to confirm no regressions
