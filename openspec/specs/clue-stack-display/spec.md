## ADDED Requirements

### Requirement: 场景/道具缩略图栈展示

`ReferencesSection` SHALL 通过 `ClueStack` 组件展示当前分镜关联的场景（`sceneNames`）与道具（`propNames`）图片缩略图，形状为圆角方形，叠放样式与角色头像栈一致，最多显示 4 个，超出部分以 `+n` 溢出徽章表示。

#### Scenario: 有资产图片时展示缩略图

- **WHEN** 场景对象存在 `scene_sheet` 路径，或道具对象存在 `prop_sheet` 路径
- **THEN** 展示对应图片，形状为圆角方形（`rounded`），尺寸与角色头像一致（`h-7 w-7`）

#### Scenario: 无资产图片时展示首字母占位

- **WHEN** 场景/道具对象不存在对应的 sheet 路径
- **THEN** 展示资产名称首字母色块（圆角方形），颜色由名称哈希值确定，与角色头像的 fallback 规则一致

#### Scenario: 分镜无关联场景/道具时不渲染

- **WHEN** `sceneNames` 与 `propNames` 均为空
- **THEN** 缩略图栈不渲染（`ClueStack` 返回 null）

#### Scenario: 超过 4 个资产时显示溢出数量

- **WHEN** 关联场景 + 道具总数超过 4 个
- **THEN** 只显示前 4 个缩略图，后续以 `+n` 灰色徽章表示剩余数量

### Requirement: 资产悬停浮窗

鼠标悬停在场景/道具缩略图（`RefThumbnail`）上时 SHALL 弹出浮窗（`RefPopover`），显示资产图片、名称、类型标签（场景/道具）及描述摘要，与角色浮窗布局一致。

#### Scenario: 悬停展示资产详情

- **WHEN** 用户将鼠标悬停在某个场景/道具缩略图上
- **THEN** 弹出浮窗，左侧显示资产图片（无图则图标占位），右侧显示资产名称和 description 首行摘要

#### Scenario: 浮窗显示场景标签

- **WHEN** 浮窗展示的资产 `kind` 为 `"scene"`
- **THEN** 名称旁显示"场景"标签（amber 色调）

#### Scenario: 浮窗显示道具标签

- **WHEN** 浮窗展示的资产 `kind` 为 `"prop"`
- **THEN** 名称旁显示"道具"标签（emerald 色调）

### Requirement: 角色浮窗显示"角色"类型标签

角色缩略图（`RefThumbnail`，`kind` 为 `"character"`）的浮窗 SHALL 在角色名称旁显示"角色"类型标签，与场景/道具浮窗的标签风格统一，便于区分实体类型。

#### Scenario: 悬停角色头像显示"角色"标签

- **WHEN** 用户将鼠标悬停在角色头像上
- **THEN** 浮窗中角色名称旁显示"角色"标签（indigo 色调）
