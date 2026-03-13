## 1. 分镜时长选择器（DurationSelector）

- [x] 1.1 将 `SegmentCard.tsx` 中的 `DurationBadge` 改造为 `DurationSelector`：`onUpdatePrompt` 存在时可点击，点击弹出 Popover；无 `onUpdatePrompt` 时保持只读外观
- [x] 1.2 在 Popover 中渲染 4s / 6s / 8s 三个选项按钮，当前值高亮
- [x] 1.3 选中新值后调用 `onUpdatePrompt(segmentId, "duration_seconds", newValue)` 并关闭 Popover
- [x] 1.4 点击 Popover 外部时关闭 Popover，时长值不变

## 2. ClueStack 组件

- [x] 2.1 新建 `frontend/src/components/ui/ClueStack.tsx`，参照 AvatarStack 结构实现线索缩略图叠放展示
- [x] 2.2 线索图片形状使用圆角方形（`rounded`），尺寸与人物头像一致（`h-7 w-7`），叠放间距使用 `-space-x-2`
- [x] 2.3 无 `clue_sheet` 时展示首字母色块（圆角方形），颜色由名称哈希值确定
- [x] 2.4 超过 4 个线索时显示 `+n` 溢出徽章
- [x] 2.5 分镜无关联线索时 ClueStack 不渲染

## 3. 线索悬停浮窗（CluePopover）

- [x] 3.1 在 `ClueStack.tsx` 内实现 `CluePopover`：左侧线索图片（无图则图标占位），右侧名称 + 类型标签 + 描述摘要
- [x] 3.2 `type === "location"` 时显示"场景"标签（amber 色调）；`type === "prop"` 时显示"道具"标签（emerald 色调）
- [x] 3.3 浮窗布局、尺寸、layer 与 AvatarPopover 保持一致

## 4. 人物浮窗增加类型标签

- [x] 4.1 修改 `AvatarStack.tsx` 中的 `AvatarPopover`：在角色名称旁新增"人物"标签（indigo 色调），与线索浮窗标签风格一致

## 5. SegmentCard 头部集成

- [x] 5.1 在 `SegmentCard.tsx` 中获取关联线索名称（从 `clues_in_segment` / `clues_in_scene` 读取，与 `getCharacterNames` 同模式）
- [x] 5.2 将 `_clues` 参数重命名为 `clues`，在头部渲染 `ClueStack`，头部右侧布局为 AvatarStack（左）+ 竖线 + ClueStack（右），两者之间用竖线（`border-l border-gray-700`）分隔
- [x] 5.3 将 `DurationBadge` 替换为 `DurationSelector`，接入 `onUpdatePrompt` 回调

## 6. 验证

- [x] 6.1 运行 `pnpm test` 确认全部测试通过
- [x] 6.2 运行 `pnpm typecheck` 确认无 TypeScript 类型错误
