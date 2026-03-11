# batch-notification-aggregation Specification

## Purpose
TBD - created by archiving change timeline-perf-and-notifications. Update Purpose after archive.
## Requirements
### Requirement: 同类变更聚合通知
当一个 SSE 变更批次包含多条同类变更时，系统 MUST 将它们聚合为一条通知，而非仅展示其中一条。

#### Scenario: 批量新增角色
- **WHEN** Agent 批量新增 3 个角色（张三、李四、王五），SSE 变更批次包含 3 条 `character:created` 变更
- **THEN** 系统展示一条聚合 toast 通知："新增了 3 个角色：张三、李四、王五"

#### Scenario: 批量新增线索
- **WHEN** Agent 批量新增 2 个线索（凶器、日记），SSE 变更批次包含 2 条 `clue:created` 变更
- **THEN** 系统展示一条聚合 toast 通知："新增了 2 个线索：凶器、日记"

#### Scenario: 单条变更保持原有格式
- **WHEN** SSE 变更批次仅包含 1 条 `character:created` 变更
- **THEN** 通知文案保持与当前行为一致（如"角色「张三」已创建"）

### Requirement: 变更分组展示
不同类型的变更 MUST 分组展示，每组独立生成一条通知。

#### Scenario: 混合类型变更
- **WHEN** 一个 SSE 批次同时包含 2 条 `character:created` 和 1 条 `episode:created` 变更
- **THEN** 系统生成两条 toast 通知：一条关于角色，一条关于剧集

### Requirement: Workspace 通知聚合
Workspace notification（非 toast 的持久通知）也 MUST 聚合展示，导航到该组第一个变更的位置。

#### Scenario: 批量角色创建的 workspace 通知
- **WHEN** Agent 批量新增 3 个角色，source 不是 "webui"
- **THEN** 生成一条 workspace notification，文案为聚合格式，点击导航到第一个角色

### Requirement: 长列表截断
当同类变更数量超过阈值时，通知文案 MUST 截断以保持可读性。

#### Scenario: 超过 5 个同类变更
- **WHEN** 一个 SSE 批次包含 8 条 `segment:updated` 变更
- **THEN** 通知文案截断展示，如"更新了 8 个分镜：seg_001、seg_002…等"

