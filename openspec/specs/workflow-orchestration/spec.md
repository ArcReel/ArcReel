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

### Requirement: 用户确认闸门由计划声明，编排 skill 只负责取得输入

哪些动作必须先取得用户输入才能推进，SHALL 由计划自身声明——`next_action.requires_confirmation` 与确认型动作（如 step1 审阅确认、旁白交付方式选择、跨档时长确认）。编排 skill SHALL 按该声明向用户取得输入后再执行对应动作，不自行判定某个动作「需不需要问」。

#### Scenario: 计划声明动作需要确认
- **WHEN** `next_action.requires_confirmation == true`
- **THEN** 编排 skill 先向用户展示该动作与计划给出的选项／原因并取得答复，答复前不执行该动作，也不改写用户的选择

#### Scenario: 计划交回选择型动作
- **WHEN** `next_action.type` 是要求用户在计划给出的 `args` 选项中作选的动作（如旁白交付方式）
- **THEN** 编排 skill 把选项原样呈现给用户，取得选择后作为参数带回工具调用，不代为默认

#### Scenario: 一步完成后决定下一步
- **WHEN** 某个动作（含其 subagent）返回
- **THEN** 编排 skill 向用户转述结构化回执后重新取计划，以新计划的 `next_action` 为准；向用户汇报与征求意见是会话礼仪，不构成阶段推进依据，也不允许据此跳过计划

### Requirement: 编排 skill 不维护阶段游标

编排 skill SHALL NOT 持有「当前处在第几步」「下一步是什么」的本地状态：无论是首次进入、中途续做，还是用户点名某个动作，起点一律由计划交回的 `next_action` 与 `steps[]` 决定。

#### Scenario: 用户只想做单个动作
- **WHEN** 用户请求某个具体动作（如"分析小说角色"）
- **THEN** 编排 skill 校验该请求与计划交回的动作一致后执行，完成即停，不自动接着往下走

#### Scenario: 用户请求继续未完成的工作
- **WHEN** 用户运行 /video-workflow 或说"继续"
- **THEN** 编排 skill 以计划交回的 `next_action` 为准继续，不按空资产 bucket、文件名、旧文件存在性或对话记忆自行定位阶段

#### Scenario: 用户点名的动作与计划不一致
- **WHEN** 用户要求的动作不是计划交回的动作
- **THEN** 编排 skill 说明计划的当前结论与差异，不越过计划直接执行

### Requirement: dispatch 参数取自计划，不由编排 skill 推导

主 agent dispatch subagent 时，任务参数 SHALL 按具体动作区分来源：目标集、预处理器等动作专属参数取自该动作的 `next_action.args`；待处理的资源 ID 在计划交回 `requested_ids` 时取自该字段——并非每个动作都会携带这一字段。上下文只传文件路径与关键参数，原文正文由 subagent 自行读取，不进主 agent context。

#### Scenario: dispatch 资产提取 subagent
- **WHEN** 主 agent dispatch `analyze-assets`
- **THEN** 传递项目名称与计划交回的范围参数（源文件清单、期望的源文修订），subagent 自行读取小说原文

#### Scenario: dispatch 单集预处理 subagent
- **WHEN** 主 agent dispatch 预处理 subagent
- **THEN** subagent 身份与集数取自 `next_action.args`，不由编排 skill 按内容模式与生成路线自行推导

#### Scenario: 计划交回待处理的资源 ID
- **WHEN** `next_action` 携带 `requested_ids`
- **THEN** 编排 skill 按该清单交派，不自行遍历目录或脚本重新算一份待生成清单

### Requirement: 生成动作按计划交派，回执按结构化契约转述

生成类动作（资产设计图、分镜图／宫格、视频、旁白配音）SHALL 由计划的受控动作驱动，编排 skill 只负责交派并按生成结果契约逐 ID 转述回执；执行载体（专职执行器 subagent 或主 agent 直呼工具）由各内容模式变体决定，不在本 spec 约束。

#### Scenario: 计划交回资产设计图生成动作
- **WHEN** `next_action.type == "generate_asset_sheets"`
- **THEN** 编排 skill 按 `requested_ids` 交派生成，完成后按 succeeded / failed / blocked 逐 ID 转述，不把整批塌成一句成败

#### Scenario: 计划交回分镜或视频生成动作
- **WHEN** `next_action.type` 是分镜图、宫格或视频生成动作
- **THEN** 编排 skill 按计划交回的目标集交派，不自行判断哪些条目缺产物

#### Scenario: 生成动作留下在途任务
- **WHEN** 计划交回等待任务的动作
- **THEN** 编排 skill 按计划给出的任务标识等待并复查计划，不轮询文件系统判断产物是否落盘
