## 1. 前端依赖与基础设施

- [x] 1.1 安装 @tanstack/react-virtual 依赖
- [x] 1.2 在 app-store 中实现 `entityRevisions: Record<string, number>` 替代 `mediaRevision: number`，提供 `invalidateEntities(keys: string[])` 和 `getEntityRevision(key: string)` 方法，保留 `invalidateAllEntities()` 作为后备全量失效

## 2. 虚拟滚动

- [x] 2.1 在 TimelineCanvas 中引入 `useVirtualizer`，配置 `estimateSize`(200px)、`overscan`(5)、`measureElement`
- [x] 2.2 将 SegmentCard 列表从 `segments.map()` 全量渲染改为 `virtualItems.map()` 绝对定位渲染
- [x] 2.3 适配 `useScrollTarget`：维护 `segmentId → virtualIndex` 映射，scrollTarget 触发时调用 `virtualizer.scrollToIndex()`，滚动完成后执行 flash 高亮

## 3. 全部消费者迁移到精确订阅

- [x] 3.1 SegmentCard：将 `mediaRevision` 订阅改为 `entityRevisions["segment:{segment_id}"]`，`<img>` 添加 `loading="lazy"`
- [x] 3.2 CharacterCard：将 `mediaRevision` 订阅改为 `entityRevisions["character:{name}"]`
- [x] 3.3 ClueCard：将 `mediaRevision` 订阅改为 `entityRevisions["clue:{name}"]`
- [x] 3.4 OverviewCanvas：将 `mediaRevision` 订阅改为 `entityRevisions["project:project"]`
- [x] 3.5 AssetSidebar：移除 `mediaRevision` props 透传，各子组件（CharacterSheetCard、ClueSheetCard）改为直接订阅 store 的对应实体 key
- [x] 3.6 AvatarStack：将 `mediaRevision` 订阅改为各头像按 `character:{name}` / `clue:{name}` 订阅
- [x] 3.7 VersionTimeMachine：将 `mediaRevision` 订阅改为 `entityRevisions["{resourceType}:{resourceId}"]`

## 4. 精确缓存失效

- [x] 4.1 修改 useProjectEventsSSE 的 `onChanges` 回调：从 SSE 变更事件的 `entity_type` + `entity_id` 构造 key，调用 `invalidateEntities(keys)` 替代 `invalidateMediaAssets()`
- [x] 4.2 修改 useProjectEventsSSE 的 `refreshProject`：不再调用 `invalidateMediaAssets()`（精确失效已在 onChanges 中处理）
- [x] 4.3 修改 useProjectAssetSync：task 完成时调用 `invalidateAllEntities()` 作为后备
- [x] 4.4 清理 app-store 中废弃的 `mediaRevision` 字段和 `invalidateMediaAssets()` 方法（确认无其他消费者后删除）

## 5. 通知聚合

- [x] 5.1 实现 `groupChangesByType(changes)` 工具函数：按 `entity_type:action` 分组变更
- [x] 5.2 实现 `formatGroupedNotificationText(group)` 和 `formatGroupedDeferredText(group)` 聚合文案函数，支持截断（超过 5 个时显示"…等"）
- [x] 5.3 修改 useProjectEventsSSE 的 `onChanges` 回调：替换 `selectNotificationChange` → 分组后每组一条 toast；替换 `selectPrimaryChange` → 分组后每组一条 workspace notification（导航到组内第一个）

## 6. 测试与验证

- [x] 6.1 为通知聚合函数（groupChangesByType、formatGroupedNotificationText）编写单元测试
- [x] 6.2 为精确缓存失效逻辑（entityRevisions 的 invalidateEntities / invalidateAllEntities）编写单元测试
- [x] 6.3 更新现有测试中对 `mediaRevision` 的引用（stores.test.ts、useProjectAssetSync.test.tsx、OverviewCanvas.test.tsx）
- [x] 6.4 运行全量前端测试（pnpm check）确保无回归
- [x] 6.5 运行全量后端测试（pytest）确保无回归
