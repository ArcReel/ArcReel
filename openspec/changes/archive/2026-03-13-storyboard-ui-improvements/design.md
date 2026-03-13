## Context

SegmentCard 是分镜板的核心卡片组件，渲染于 TimelineCanvas 的虚拟滚动列表中。当前头部区域仅展示分镜 ID、只读时长徽章和人物头像栈。分镜的 `duration_seconds`（4/6/8s）已在后端数据模型和 PATCH API 中完整支持，但前端没有提供修改入口。线索（`clues_in_segment` / `clues_in_scene`）字段同样存在于数据模型，但 `SegmentCard` 接收后将其标记为 `_clues` 而从未渲染。

## Goals / Non-Goals

**Goals:**
- 让用户在卡片头部直接切换分镜时长（4/6/8s），剧集总时长随之联动
- 在卡片头部展示关联线索的图片缩略图，与人物头像栈并排
- 悬停浮窗统一增加类型标签，区分"人物"与"场景/道具"

**Non-Goals:**
- 不修改后端 API 或数据模型
- 不改变 TimelineCanvas 的虚拟滚动逻辑
- 不在 SegmentCard 内容区（三列）添加任何新信息

## Decisions

### 决策 1：时长选择器使用 Popover 而非点击循环

**选择**：点击时长徽章弹出 Popover，列出 4s / 6s / 8s 三个按钮，当前值高亮。

**理由**：直接循环切换（4→6→8→4）不直观，用户无法一次看到全部选项。Popover 复用已有的 `Popover` 组件，实现代价低，且与项目中其他弹出交互风格一致。

**备选方案**：行内三段 Segmented Control（始终可见）——占用水平空间，在头部宽度有限时会挤压 ID 徽章和头像区。

### 决策 2：时长变更通过现有 onUpdatePrompt 通道传递

**选择**：调用 `onUpdatePrompt(segmentId, "duration_seconds", newValue)`，复用 `StudioCanvasRouter` → `API.updateSegment` / `API.updateScene` → `refreshProject()` 的完整链路。

**理由**：无需增加新 prop 或新 callback，后端 PATCH 接口已支持 `duration_seconds`，总时长在 `refreshProject()` 后从 segments 重新聚合。

### 决策 3：ClueStack 作为独立组件，与 AvatarStack 并排

**选择**：新建 `ClueStack.tsx`（位于 `frontend/src/components/ui/`），不修改 AvatarStack 的泛化能力。SegmentCard 头部布局为：AvatarStack（人物）在左，竖线分隔，ClueStack（线索）在右。

**理由**：人物与线索语义不同（人物有 character_sheet，线索有 clue_sheet；hover 浮窗内容不同），强行合并会增加 AvatarStack 的复杂度。复制 AvatarStack 的结构模式（图片 + 首字母 fallback + hover popover + overflow badge）成本低且互不干扰。

**形状**：线索缩略图使用 `rounded`（圆角方形）而非 `rounded-full`，与左侧 Lorebook 中线索卡片的图片风格一致。

### 决策 4：浮窗类型标签统一样式

人物浮窗（AvatarPopover）在名称右侧新增 `人物` 标签（indigo）；线索浮窗依据 `Clue.type` 显示 `场景`（amber）或 `道具`（emerald）。两者均为小型 Badge，保持浮窗内容结构不变。

## Risks / Trade-offs

- **总时长联动依赖后端刷新**：时长变更后需等待 `refreshProject()` 完成才更新头部总时长，存在约 200-500ms 延迟。由于分镜时长切换是低频操作，不做乐观更新。
- **ClueStack 图片缺失率较高**：早期项目的线索通常没有 `clue_sheet`，fallback 为首字母色块，功能完整但视觉效果依赖用户是否上传了线索图片。
