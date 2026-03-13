## Why

分镜板的 SegmentCard 存在两处信息缺失：分镜时长（4/6/8s）目前只读无法在界面上直接修改，导致用户必须借助其他途径调整；关联线索（场景/道具）字段在数据模型中已存在，但从未在卡片头部展示，造成创作上下文不完整。

## What Changes

- **DurationBadge → DurationSelector**：分镜时长标签由只读改为可交互，点击后弹出 Popover 选择 4s / 6s / 8s，选中后通过现有 `onUpdatePrompt` 通道写入后端；剧集 header 的总时长随数据刷新自动联动。
- **新增 ClueStack 组件**：在 SegmentCard 头部右侧展示关联线索缩略图（圆角方形，与左侧 Lorebook 图片风格一致），悬停时弹出浮窗，显示线索名称、图片及类型标签（场景 / 道具）。
- **人物浮窗增加"人物"标签**：AvatarPopover 在角色名旁新增 `人物` 类型标签，与线索浮窗风格统一，方便区分。

## Capabilities

### New Capabilities

- `segment-duration-selector`：SegmentCard 头部的分镜时长可通过弹出选择器切换（4/6/8s），并联动更新剧集总时长显示。
- `clue-stack-display`：SegmentCard 头部展示关联线索的图片缩略图栈，悬停浮窗显示名称、图片与类型标签（场景/道具）；人物浮窗同步新增"人物"类型标签。

### Modified Capabilities

（无现有 spec 需要变更）

## Impact

- 纯前端改动，不涉及后端 API 或数据模型变更
- 修改文件：`frontend/src/components/canvas/timeline/SegmentCard.tsx`、`frontend/src/components/ui/AvatarStack.tsx`
- 新增文件：`frontend/src/components/ui/ClueStack.tsx`
- 后端 PATCH `/projects/{name}/segments/{segment_id}` 已支持 `duration_seconds` 字段，无需改动
