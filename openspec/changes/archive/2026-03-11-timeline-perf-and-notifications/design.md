## Context

ArcReel 前端的分镜场景板（TimelineCanvas）使用简单的 `overflow-y-auto` 垂直堆叠所有 SegmentCard，没有虚拟滚动或懒加载。一个剧集通常有 30-100 个分镜，页面加载时会同时发起所有图片和视频的请求。

当任意资源变更时，`invalidateMediaAssets()` 递增全局 `mediaRevision` 计数器，导致所有订阅该值的组件（SegmentCard、CharacterCard、ClueCard、OverviewCanvas、AssetSidebar、AvatarStack、VersionTimeMachine）同时触发媒体 URL `?v=N` 变化，浏览器重新加载全部资源。

Agent 批量操作（如一次新增 5 个角色）时，后端 diff 正确产生多条变更事件，但前端 `selectPrimaryChange()` / `selectNotificationChange()` 仅从数组中选出 1 条展示。

## Goals / Non-Goals

**Goals:**
- 将 Timeline 并发媒体请求数从 N（分镜总数）降低到视口可见数量 + overscan（约 8-12 个）
- 单个实体变更时，其他实体的媒体资源不被重新加载（覆盖全部 7 个消费组件）
- Agent 批量操作时，用户能感知到所有变更（聚合通知）

**Non-Goals:**
- 后端 SSE 协议变更（当前事件信息已足够）
- 服务端缓存头（ETag/Last-Modified）优化
- 分页加载 / 无限滚动
- 通知中心 / 通知历史

## Decisions

### 1. 虚拟滚动选型：@tanstack/react-virtual

**选择**：@tanstack/react-virtual v3
**替代方案**：react-window、react-virtuoso
**理由**：
- 原生支持动态高度（`measureElement` + ResizeObserver），SegmentCard 有展开/折叠态
- 无 UI 侵入性，仅提供 virtualizer hook，与现有 Tailwind 样式体系兼容
- 项目已使用 @tanstack 生态（react-query 等），保持技术栈一致性
- `estimateSize` 设为 200px 预估高度，overscan 设为 5

### 2. 懒加载策略：原生 + 虚拟滚动

**选择**：虚拟滚动自然实现懒加载（不在视口的 SegmentCard 不渲染），视口内的 `<img>` 额外添加 `loading="lazy"` 作为二级保险
**理由**：虚拟滚动已经从根本上解决了问题，`loading="lazy"` 仅作为 overscan 区域内图片的补充优化，无需额外的 IntersectionObserver 逻辑

### 3. 缓存失效：按实体的版本号（key 为 entity_type:entity_id）

**选择**：`entityRevisions: Record<string, number>`，key 格式为 `entity_type:entity_id`（如 `segment:seg_001`、`character:张三`、`clue:凶器`、`project:project`）
**替代方案**：
- 按文件路径的版本号 — 角色/线索的文件路径不确定性，需要额外推导逻辑
- 前端 diff 前后 scripts 数据 — 复杂度高，不可靠
**理由**：
- SSE 事件中 `entity_type` + `entity_id` 直接可用，无需任何推导
- 统一覆盖所有 7 个消费组件，各组件按自身实体 key 订阅
- Worker 批量发送路径（`emit_project_change_batch`）正确处理了首次生成和重新生成
- 保留 `invalidateAllEntities()` 作为后备（task 轮询通道完成时无具体 key 信息）

**消费者迁移清单：**

| 组件 | 原订阅 | 新订阅 key |
|------|--------|-----------|
| SegmentCard | `mediaRevision` | `segment:{segment_id}` |
| CharacterCard | `mediaRevision` | `character:{character_name}` |
| ClueCard | `mediaRevision` | `clue:{clue_name}` |
| OverviewCanvas | `mediaRevision` | `project:project` |
| AssetSidebar | `mediaRevision`（props 透传） | `character:{name}` / `clue:{name}`（各子组件独立订阅） |
| AvatarStack | `mediaRevision` | `character:{name}` / `clue:{name}` |
| VersionTimeMachine | `mediaRevision` | `{resourceType}:{resourceId}`（动态 key） |

### 4. 滚动定位适配

**选择**：维护 `segmentId → virtualIndex` 映射，scrollTarget 触发时调用 `virtualizer.scrollToIndex()`
**理由**：虚拟滚动下目标 segment 可能不在 DOM 中，无法使用 `getElementById` + `scrollIntoView()`

### 5. 通知聚合：按 entity_type:action 分组

**选择**：将同批次 changes 按 `entity_type:action` 分组，每组生成一条聚合文案
**替代方案**：每条变更单独弹 toast
**理由**：批量 5 个角色弹 5 个 toast 体验差，聚合为"AI 新增了 3 个角色：张三、李四、王五"更友好

## Risks / Trade-offs

- **[动态高度预估偏差]** → `estimateSize` 不准确时可能出现滚动跳跃；通过 `measureElement` 实时修正来缓解
- **[entityRevisions 内存增长]** → 大项目可能有较多 key；实际上 100 分镜 + 20 角色 + 10 线索 ≈ 130 个 key，可忽略
- **[全量失效后备路径的体验]** → `useProjectAssetSync` 中 task 完成时仍会全量失效；这是极少数情况（SSE 断连时的补偿），可接受
- **[AssetSidebar props 重构]** → 当前 AssetSidebar 通过 props 接收 `mediaRevision`，需改为子组件各自订阅 store；改动面稍大但更符合 zustand 最佳实践
- **[聚合通知文案截断]** → 超过 5 个同类变更时截断为"AI 新增了 5 个角色：张三、李四…等"
