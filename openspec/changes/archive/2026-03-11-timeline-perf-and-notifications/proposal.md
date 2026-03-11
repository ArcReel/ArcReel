## Why

分镜场景板（Timeline）在 30-100 个分镜的剧集中存在严重的性能和体验问题：全量 DOM 渲染导致图片/视频同时加载撑爆带宽；任意资源变更触发全局缓存失效导致所有媒体重载；Agent 批量操作时通知仅展示其中一条导致用户感知不全。

## What Changes

- **虚拟滚动**：TimelineCanvas 引入 @tanstack/react-virtual，仅渲染视口附近的 SegmentCard，从根本上减少并发请求数
- **懒加载**：`<img>` 添加 `loading="lazy"`，`<video>` 仅在进入视口时设置 `src`
- **精确缓存失效**：将全局 `mediaRevision: number` 替换为 `entityRevisions: Record<string, number>`（key 为 `entity_type:entity_id`），利用 SSE 事件中的 `entity_type` + `entity_id` 直接构造 key，仅递增变更实体的版本号。覆盖所有 7 个消费者：SegmentCard、CharacterCard、ClueCard、OverviewCanvas、AssetSidebar、AvatarStack、VersionTimeMachine
- **滚动定位适配**：`useScrollTarget` 适配虚拟滚动，改用 `virtualizer.scrollToIndex()` 替代 `scrollIntoView()`
- **通知聚合**：`useProjectEventsSSE` 中将同类变更按 `entity_type:action` 分组，toast 和 workspace notification 展示聚合文案（如"AI 新增了 3 个角色：张三、李四、王五"）

## Capabilities

### New Capabilities
- `timeline-virtual-scroll`: 时间线虚拟滚动与媒体懒加载，减少并发网络请求和 DOM 数量
- `precise-cache-invalidation`: 按实体粒度的缓存失效机制，替代全局 mediaRevision，覆盖全部 7 个媒体消费组件
- `batch-notification-aggregation`: SSE 变更通知聚合，将同批次同类变更合并为一条用户可读通知

### Modified Capabilities
（无需修改已有 spec 级行为）

## Impact

- **前端依赖**：新增 `@tanstack/react-virtual`
- **前端组件**：TimelineCanvas、SegmentCard（MediaColumn）、CharacterCard、ClueCard、OverviewCanvas、AssetSidebar、AvatarStack、VersionTimeMachine、useScrollTarget hook
- **前端 Store**：app-store（entityRevisions 替换 mediaRevision）
- **前端 Hooks**：useProjectEventsSSE（精确失效 + 通知聚合）、useProjectAssetSync（保留全量失效作为后备）
- **后端**：无变更（SSE 事件已包含足够信息）
