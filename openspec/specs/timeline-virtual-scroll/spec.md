# timeline-virtual-scroll Specification

## Purpose
TBD - created by archiving change timeline-perf-and-notifications. Update Purpose after archive.
## Requirements
### Requirement: 虚拟滚动渲染
TimelineCanvas MUST 使用虚拟滚动技术渲染 SegmentCard 列表，仅将视口附近的 SegmentCard 挂载到 DOM 中。

#### Scenario: 大量分镜的初始加载
- **WHEN** 用户打开包含 50 个分镜的剧集
- **THEN** DOM 中仅渲染视口可见的 SegmentCard 加上 overscan 数量（约 8-12 个），而非全部 50 个

#### Scenario: 滚动浏览
- **WHEN** 用户向下滚动时间线
- **THEN** 新进入视口范围的 SegmentCard 被渲染，离开视口范围的 SegmentCard 被卸载

### Requirement: 动态高度支持
虚拟滚动 MUST 支持 SegmentCard 的动态高度，包括展开/折叠态导致的高度变化。

#### Scenario: 展开折叠卡片
- **WHEN** 用户展开某个 SegmentCard 导致其高度变化
- **THEN** 虚拟滚动列表正确调整后续项的位置，不出现跳跃或重叠

#### Scenario: 预估高度与实际高度差异
- **WHEN** SegmentCard 实际渲染高度与预估值不同
- **THEN** virtualizer 通过 measureElement 自动修正，滚动位置保持平滑

### Requirement: 图片懒加载
视口内的 `<img>` 标签 MUST 使用浏览器原生懒加载属性。

#### Scenario: overscan 区域的图片
- **WHEN** SegmentCard 位于 overscan 区域（已渲染但未进入可视视口）
- **THEN** 其 `<img>` 标签具有 `loading="lazy"` 属性，浏览器延迟加载直到接近可视区域

### Requirement: 滚动定位适配
Agent 或系统触发的滚动定位（scrollTarget）MUST 在虚拟滚动环境下正常工作。

#### Scenario: Agent 触发滚动到不在 DOM 中的分镜
- **WHEN** scrollTarget 指向一个当前不在 DOM 中的 segment ID
- **THEN** 系统通过 virtualizer.scrollToIndex 滚动到目标位置，目标 SegmentCard 被渲染并可见

#### Scenario: 滚动定位后的高亮
- **WHEN** 滚动定位到目标 segment 后
- **THEN** 目标 SegmentCard 执行 flash 高亮动画，与当前行为一致

