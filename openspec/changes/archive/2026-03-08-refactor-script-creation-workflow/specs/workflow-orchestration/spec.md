## ADDED Requirements

### Requirement: manga-workflow 编排 skill 须具备项目状态检测能力

manga-workflow skill 被加载后，SHALL 自动检测当前项目的工作流状态，基于 project.json 和文件系统判断当前所处阶段。

#### Scenario: 新项目无角色和线索
- **WHEN** project.json 中 characters 和 clues 为空
- **THEN** 编排 skill 判定当前阶段为"全局角色/线索设计"，指引主 agent dispatch `analyze-characters-clues` subagent

#### Scenario: 已有角色但无 drafts 中间文件
- **WHEN** project.json 中 characters 非空，但 `drafts/episode_{N}/` 目录不存在或为空
- **THEN** 编排 skill 判定当前阶段为"单集预处理"，指引主 agent dispatch 对应模式的预处理 subagent

#### Scenario: 已有 drafts 但无 scripts
- **WHEN** `drafts/episode_{N}/` 中间文件存在，但 `scripts/episode_{N}.json` 不存在
- **THEN** 编排 skill 判定当前阶段为"JSON 剧本生成"，指引主 agent dispatch `create-episode-script` subagent

#### Scenario: 已有 scripts 但缺少资产
- **WHEN** `scripts/episode_{N}.json` 存在，但 characters/ 或 storyboards/ 或 videos/ 中有缺失资产
- **THEN** 编排 skill 判定当前阶段为"资产生成"，指引主 agent dispatch 对应的资产生成 subagent

### Requirement: 编排 skill 须定义阶段间的 dispatch 和确认协议

每个阶段的 subagent 返回后，主 agent SHALL 向用户展示结果摘要并等待确认，确认后才进入下一阶段。

#### Scenario: subagent 返回角色/线索提取结果
- **WHEN** `analyze-characters-clues` subagent 完成并返回
- **THEN** 主 agent 展示角色/线索数量和名称列表摘要，使用 AskUserQuestion 获取用户确认，确认后进入下一阶段

#### Scenario: 用户拒绝 subagent 结果
- **WHEN** 用户对某阶段的结果不满意
- **THEN** 主 agent 可选择重新 dispatch 同一 subagent（附加用户反馈）或允许用户手动编辑后继续

#### Scenario: 用户选择跳过某阶段
- **WHEN** 用户明确表示跳过当前阶段
- **THEN** 主 agent 跳过该阶段，直接进入下一阶段

### Requirement: 编排 skill 须支持灵活入口点

manga-workflow SHALL 支持从任意阶段开始执行，而非强制从头开始。

#### Scenario: 用户只想做角色设计
- **WHEN** 用户请求"分析小说角色"但不需要创建剧本
- **THEN** 主 agent 只 dispatch `analyze-characters-clues` subagent，完成后不自动进入下一阶段

#### Scenario: 用户已有角色想直接创建剧本
- **WHEN** project.json 中已有角色/线索定义，用户请求创建某集剧本
- **THEN** 编排 skill 跳过角色/线索提取阶段，直接进入单集预处理阶段

#### Scenario: 用户想续做上次中断的工作
- **WHEN** 用户运行 /manga-workflow，项目有部分完成的工作
- **THEN** 编排 skill 通过状态检测自动定位到上次中断的阶段，从该阶段继续

### Requirement: 编排 skill 须正确传递上下文给 subagent

主 agent dispatch subagent 时，SHALL 只传递该 subagent 任务所需的最小上下文（文件路径和关键参数），而非大块原始内容。

#### Scenario: dispatch 角色/线索提取 subagent
- **WHEN** 主 agent dispatch `analyze-characters-clues`
- **THEN** 传递项目名称、source 目录路径、已有角色/线索名称列表；subagent 自行读取小说原文

#### Scenario: dispatch 单集预处理 subagent
- **WHEN** 主 agent dispatch 预处理 subagent
- **THEN** 传递项目名称、集数、content_mode、角色/线索名称列表；subagent 自行读取对应的小说文本

### Requirement: 资产生成阶段通过 subagent 调用 skill

生成类 skill（generate-characters、generate-clues、generate-storyboard、generate-video）SHALL 通过 subagent 调用，而非主 agent 直接调用。

#### Scenario: 生成角色设计图
- **WHEN** 编排进入角色设计阶段
- **THEN** 主 agent dispatch subagent，subagent 内部通过 Bash 工具调用 generate_character.py 脚本，返回生成结果摘要

#### Scenario: 批量生成分镜图
- **WHEN** 编排进入分镜图生成阶段
- **THEN** 主 agent dispatch subagent，subagent 内部调用 generate_storyboard.py 脚本，处理所有待生成的分镜图，返回成功/失败汇总摘要
