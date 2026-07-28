## ADDED Requirements

### Requirement: 线索缩略图栈展示

SegmentCard 头部 SHALL 在角色头像栈左侧展示当前分镜关联线索（`clues_in_segment` / `clues_in_scene`）的图片缩略图，形状为圆角方形，叠放样式与角色头像栈一致，最多显示 4 个，超出部分以 `+n` 溢出徽章表示。

#### Scenario: 有线索图片时展示缩略图

- **WHEN** 线索对象存在 `clue_sheet` 路径
- **THEN** 展示对应图片，形状为圆角方形（`rounded`），尺寸与角色头像一致（`h-7 w-7`）

#### Scenario: 无线索图片时展示首字母占位

- **WHEN** 线索对象不存在 `clue_sheet` 路径
- **THEN** 展示线索名称首字母色块（圆角方形），颜色由名称哈希值确定，与角色头像的 fallback 规则一致

#### Scenario: 分镜无关联线索时不渲染

- **WHEN** 分镜的 `clues_in_segment` / `clues_in_scene` 为空数组
- **THEN** 线索缩略图栈不渲染，头部右侧仅显示角色头像栈

#### Scenario: 超过 4 个线索时显示溢出数量

- **WHEN** 分镜关联线索超过 4 个
- **THEN** 只显示前 4 个缩略图，后续以 `+n` 灰色徽章表示剩余数量

### Requirement: 线索悬停浮窗

鼠标悬停在线索缩略图上时 SHALL 弹出浮窗，显示线索图片、名称、类型标签（场景/道具）及描述摘要，布局与角色浮窗一致。

#### Scenario: 悬停展示线索详情

- **WHEN** 用户将鼠标悬停在某个线索缩略图上
- **THEN** 弹出浮窗，左侧显示线索图片（无图则图标占位），右侧显示线索名称和一行描述摘要

#### Scenario: 浮窗显示类型标签

- **WHEN** 浮窗展示时，线索 `type` 为 `"location"`
- **THEN** 名称旁显示"场景"标签（amber 色调）

#### Scenario: 浮窗显示道具标签

- **WHEN** 浮窗展示时，线索 `type` 为 `"prop"`
- **THEN** 名称旁显示"道具"标签（emerald 色调）

### Requirement: 角色浮窗增加"角色"类型标签

AvatarPopover SHALL 在角色名称旁新增"角色"类型标签，与线索浮窗的标签风格统一，便于区分两种实体类型。

#### Scenario: 悬停角色头像显示"角色"标签

- **WHEN** 用户将鼠标悬停在角色头像上
- **THEN** 浮窗中角色名称旁显示"角色"标签（indigo 色调）
