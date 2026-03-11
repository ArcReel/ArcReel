# precise-cache-invalidation Specification

## Purpose
TBD - created by archiving change timeline-perf-and-notifications. Update Purpose after archive.
## Requirements
### Requirement: 按实体粒度的版本跟踪
系统 MUST 为每个实体维护独立的版本号（key 格式 `entity_type:entity_id`），替代全局 mediaRevision 计数器。所有媒体资产消费者（SegmentCard、CharacterCard、ClueCard、OverviewCanvas、AssetSidebar、AvatarStack、VersionTimeMachine）都必须迁移到新机制。

#### Scenario: 单个分镜图生成完成
- **WHEN** SSE 事件报告 segment "seg_001" 的 storyboard_ready
- **THEN** 仅 `segment:seg_001` 的版本号递增，其他实体的版本号不变

#### Scenario: 单个视频生成完成
- **WHEN** SSE 事件报告 segment "seg_003" 的 video_ready
- **THEN** 仅 `segment:seg_003` 的版本号递增，其他实体的版本号不变

#### Scenario: 角色设计图生成完成
- **WHEN** SSE 事件报告 character "张三" 的 updated
- **THEN** 仅 `character:张三` 的版本号递增，其他实体的版本号不变

#### Scenario: 线索设计图生成完成
- **WHEN** SSE 事件报告 clue "凶器" 的 updated
- **THEN** 仅 `clue:凶器` 的版本号递增，其他实体的版本号不变

#### Scenario: 项目元数据更新
- **WHEN** SSE 事件报告 project 的 updated
- **THEN** `project:project` 的版本号递增

### Requirement: 从 SSE 事件直接构造版本 key
系统 MUST 从 SSE 变更事件的 `entity_type` 和 `entity_id` 字段直接构造版本 key，无需推导文件路径。

#### Scenario: storyboard_ready 事件
- **WHEN** 收到 `entity_type: "segment"`, `entity_id: "seg_005"`, `action: "storyboard_ready"` 的变更事件
- **THEN** 递增 key 为 `segment:seg_005` 的版本号

#### Scenario: character updated 事件
- **WHEN** 收到 `entity_type: "character"`, `entity_id: "张三"`, `action: "updated"` 的变更事件
- **THEN** 递增 key 为 `character:张三` 的版本号

#### Scenario: clue updated 事件
- **WHEN** 收到 `entity_type: "clue"`, `entity_id: "凶器"`, `action: "updated"` 的变更事件
- **THEN** 递增 key 为 `clue:凶器` 的版本号

### Requirement: 各组件精确订阅
每个媒体消费组件 MUST 仅订阅与其相关实体的版本号。

#### Scenario: SegmentCard 精确订阅
- **WHEN** segment "seg_001" 的分镜图生成完成
- **THEN** 仅 segment "seg_001" 的 SegmentCard 触发媒体 URL 变更和重渲染，其他 SegmentCard 不受影响

#### Scenario: CharacterCard 精确订阅
- **WHEN** character "张三" 的设计图生成完成
- **THEN** 仅 "张三" 的 CharacterCard、对应的 AvatarStack 头像和 AssetSidebar 条目触发重渲染，其他角色不受影响

#### Scenario: ClueCard 精确订阅
- **WHEN** clue "凶器" 的设计图生成完成
- **THEN** 仅 "凶器" 的 ClueCard 和 AssetSidebar 条目触发重渲染

#### Scenario: OverviewCanvas 精确订阅
- **WHEN** 项目风格图更新
- **THEN** 仅 OverviewCanvas 中的风格图片触发重渲染

#### Scenario: VersionTimeMachine 精确订阅
- **WHEN** 某个资源的新版本生成完成
- **THEN** VersionTimeMachine 订阅当前展示资源对应的实体 key，仅在该实体变更时重新拉取版本列表

### Requirement: 全量失效后备
当无法确定具体变更实体时（如 task 轮询通道），系统 MUST 保留全量缓存失效作为后备机制。

#### Scenario: task 完成但无 SSE 事件
- **WHEN** useProjectAssetSync 检测到 task 从非 succeeded 变为 succeeded
- **THEN** 调用全量失效方法，所有已跟踪实体的版本号统一递增

### Requirement: 重新生成资产
资产重新生成时，缓存失效机制 MUST 正确触发。

#### Scenario: 分镜图重新生成
- **WHEN** 用户重新生成 segment "seg_001" 的分镜图（文件路径不变但内容更新）
- **THEN** Worker 发送 storyboard_ready 事件，前端递增 `segment:seg_001` 版本号，浏览器加载新内容

#### Scenario: 角色设计图重新生成
- **WHEN** 用户重新生成 character "张三" 的设计图
- **THEN** Worker 发送 character updated 事件，前端递增 `character:张三` 版本号，浏览器加载新内容

