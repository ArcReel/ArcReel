## ADDED Requirements

### Requirement: video-workflow 编排 skill 须按服务端权威计划路由

video-workflow skill 被加载后，SHALL 调用 `mcp__arcreel__get_workflow_plan` 取得权威计划，按 `next_action` 决定下一步，不自行读 project.json 或探测文件系统推断阶段。

#### Scenario: 计划交回资产分析动作
- **WHEN** `next_action.type == "analyze_assets"`
- **THEN** 编排 skill 指引主 agent dispatch `analyze-assets` subagent，不另行判断角色是否为空

#### Scenario: 计划交回单集预处理动作
- **WHEN** `next_action.type == "prepare_step1"`
- **THEN** 编排 skill 按 `next_action.args.preprocessor` dispatch 对应的预处理 subagent，不按内容模式与生成路线自己推

#### Scenario: 计划交回剧本生成动作
- **WHEN** `next_action.type == "generate_script"`
- **THEN** 编排 skill 指引主 agent dispatch `create-episode-script` subagent，不按 `scripts/` 下是否有文件自行判定

#### Scenario: 计划报出 blockers
- **WHEN** `blockers` 非空或 `next_action.type == "none"`
- **THEN** 编排 skill 向用户展示 blockers 并停止一切变更，不入队任何任务

### Requirement: 编排 skill 须定义阶段间的 dispatch 和确认协议

每个阶段的 subagent 返回后，主 agent SHALL 向用户展示结果摘要并等待确认，确认后才进入下一阶段。

#### Scenario: subagent 返回资产提取结果
- **WHEN** `analyze-assets` subagent 完成并返回
- **THEN** 主 agent 展示角色 / 场景 / 道具数量和名称列表摘要，使用 AskUserQuestion 获取用户确认，确认后进入下一阶段

#### Scenario: 用户拒绝 subagent 结果
- **WHEN** 用户对某阶段的结果不满意
- **THEN** 主 agent 可选择重新 dispatch 同一 subagent（附加用户反馈）或允许用户手动编辑后继续

#### Scenario: 用户选择跳过某阶段
- **WHEN** 用户明确表示跳过当前阶段
- **THEN** 主 agent 跳过该阶段，直接进入下一阶段

### Requirement: 编排 skill 须支持灵活入口点

video-workflow SHALL 支持从任意阶段开始执行，而非强制从头开始。

#### Scenario: 用户只想做角色设计
- **WHEN** 用户请求"分析小说角色"但不需要创建剧本
- **THEN** 主 agent 只 dispatch `analyze-assets` subagent，完成后不自动进入下一阶段

#### Scenario: 用户已有角色想直接创建剧本
- **WHEN** 用户请求创建某集剧本，计划的 `next_action.type` 已越过 `analyze_assets`
- **THEN** 编排 skill 从计划交回的动作开始，不重跑资产提取

#### Scenario: 用户想续做上次中断的工作
- **WHEN** 用户运行 /video-workflow，项目有部分完成的工作
- **THEN** 编排 skill 以计划交回的 `next_action` 为准继续，不自行定位阶段

### Requirement: 编排 skill 须正确传递上下文给 subagent

主 agent dispatch subagent 时，SHALL 只传递该 subagent 任务所需的最小上下文（文件路径和关键参数），而非大块原始内容。

#### Scenario: dispatch 资产提取 subagent
- **WHEN** 主 agent dispatch `analyze-assets`
- **THEN** 传递项目名称、source 目录路径、已有角色 / 场景 / 道具名称列表；subagent 自行读取小说原文

#### Scenario: dispatch 单集预处理 subagent
- **WHEN** 主 agent dispatch 预处理 subagent
- **THEN** 传递项目名称、集数、content_mode、角色 / 场景 / 道具名称列表；subagent 自行读取对应的小说文本

### Requirement: 资产生成阶段通过 subagent 调用 skill

生成类 skill（generate-assets、generate-storyboard、generate-video）SHALL 通过 subagent 调用，而非主 agent 直接调用。

#### Scenario: 生成角色设计图
- **WHEN** 编排进入角色设计阶段
- **THEN** 主 agent dispatch `generate-assets` subagent，subagent 内部调用 `mcp__arcreel__generate_assets` 工具入队生成，返回生成结果摘要

#### Scenario: 批量生成分镜图
- **WHEN** 编排进入分镜图生成阶段
- **THEN** 主 agent dispatch subagent，subagent 内部调用 `mcp__arcreel__generate_storyboards` 工具，处理所有待生成的分镜图，返回成功/失败汇总摘要
