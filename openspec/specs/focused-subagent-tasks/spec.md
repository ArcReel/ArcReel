## ADDED Requirements

### Requirement: 每个 subagent 模板须定义清晰的输入/输出契约

每个 subagent 定义文件（`.claude/agents/*.md`）SHALL 在 description 和 system prompt 中明确声明其输入参数、输出格式和内部调用的 skill/脚本。

#### Scenario: analyze-characters-clues subagent 契约
- **WHEN** 该 subagent 被 dispatch
- **THEN** 它接收项目名称和 source 目录路径作为输入，自行读取小说原文，分析并提取角色表和线索表，通过 Bash 调用 `add_characters_clues.py` 脚本写入 project.json，返回角色/线索的摘要列表

#### Scenario: split-narration-segments subagent 契约
- **WHEN** 该 subagent 被 dispatch
- **THEN** 它接收项目名称、集数、本集小说文本范围作为输入，按朗读节奏拆分片段（约 4 秒/片段），标记 segment_break，保存 `drafts/episode_{N}/step1_segments.md`，返回片段数量和总时长摘要

#### Scenario: normalize-drama-script subagent 契约
- **WHEN** 该 subagent 被 dispatch
- **THEN** 它接收项目名称、集数作为输入，首次生成时通过 Bash 调用 `normalize_drama_script.py` 脚本（使用 gemini-3.1-pro-preview 模型）生成规范化剧本和镜头预算，保存 `drafts/episode_{N}/step1_normalized_script.md` 和 `step2_shot_budget.md`，返回场景数量和镜头分布摘要；后续修改时由 subagent 直接编辑已有的 Markdown 文件

#### Scenario: create-episode-script subagent 契约
- **WHEN** 该 subagent 被 dispatch
- **THEN** 它接收项目名称和集数作为输入，预加载 generate-script skill，调用 generate_script.py 脚本生成 JSON，验证输出，返回生成结果摘要

### Requirement: 每个 subagent 须为单任务聚焦设计

每个 subagent SHALL 只完成一个聚焦的任务并返回，不得在内部包含需要用户确认的多步工作流。

#### Scenario: subagent 内部不得使用 AskUserQuestion 进行步骤间确认
- **WHEN** subagent 执行其聚焦任务
- **THEN** subagent 独立完成全部工作后返回结果，不在中间步骤使用 AskUserQuestion 等待用户确认

#### Scenario: subagent 遇到歧义可请求澄清
- **WHEN** subagent 执行过程中遇到无法独立判断的关键歧义（如小说中角色名不明确）
- **THEN** subagent 可以使用 AskUserQuestion 一次性请求澄清，但不得用于多步流程控制

### Requirement: 预处理 subagent 须按内容模式独立定义

说书模式和剧集动画模式 SHALL 使用各自独立的 subagent 定义，而非共用一个带参数切换的 subagent。

#### Scenario: narration 模式使用 split-narration-segments
- **WHEN** project 的 content_mode 为 "narration"
- **THEN** 编排 skill 指引主 agent dispatch `split-narration-segments` subagent，执行片段拆分（按朗读节奏、标记 segment_break、标记对话片段），输出 step1_segments.md

#### Scenario: drama 模式使用 normalize-drama-script
- **WHEN** project 的 content_mode 为 "drama"
- **THEN** 编排 skill 指引主 agent dispatch `normalize-drama-script` subagent，执行规范化剧本（结构化场景、时间、地点、人物）+ 镜头预算（预估镜头数、标记 segment_break），输出 step1_normalized_script.md 和 step2_shot_budget.md

### Requirement: create-episode-script subagent 须预加载 generate-script skill

`create-episode-script` subagent 的 frontmatter SHALL 通过 `skills` 字段预加载 `generate-script` skill。

#### Scenario: skill 内容在 subagent 启动时注入
- **WHEN** subagent 被 dispatch
- **THEN** generate-script skill 的完整内容已在 subagent context 中，subagent 可按 skill 指示调用 generate_script.py 脚本

#### Scenario: subagent 验证生成结果
- **WHEN** generate_script.py 脚本执行完成
- **THEN** subagent 验证 scripts/episode_{N}.json 存在且通过数据验证，如有错误则尝试修正后重新生成

### Requirement: 删除旧的多步 subagent 定义

`novel-to-narration-script.md` 和 `novel-to-storyboard-script.md` SHALL 被删除，替换为新的聚焦 subagent 模板。

#### Scenario: 旧 agent 文件被移除
- **WHEN** 重构完成
- **THEN** `agent_runtime_profile/.claude/agents/` 目录中不再包含 `novel-to-narration-script.md` 和 `novel-to-storyboard-script.md`

#### Scenario: 新 agent 文件就位
- **WHEN** 重构完成
- **THEN** `agent_runtime_profile/.claude/agents/` 目录中包含 `analyze-characters-clues.md`、`split-narration-segments.md`、`normalize-drama-script.md`、`create-episode-script.md` 四个聚焦 subagent 定义

### Requirement: 须提供角色/线索写入的 CLI 脚本

SHALL 提供 `add_characters_clues.py` 脚本，封装 `ProjectManager.add_characters_batch()` + `add_clues_batch()` + `validate_project()`，供 subagent 通过 Bash 工具调用。

#### Scenario: 批量添加角色和线索
- **WHEN** subagent 通过 Bash 调用 `add_characters_clues.py` 并传入 JSON 格式的角色/线索数据
- **THEN** 脚本将角色/线索写入 project.json，调用 validate_project 验证，打印成功/失败摘要

#### Scenario: 已存在的角色自动跳过
- **WHEN** 传入的角色名在 project.json 中已存在
- **THEN** 脚本跳过该角色（不覆盖已有数据），在输出中标注"已存在，跳过"

#### Scenario: 脚本在 settings.json 中被放行
- **WHEN** 重构完成
- **THEN** `settings.json` 的 `permissions.allow` 中包含该脚本的 Bash 执行权限

### Requirement: 须提供 drama 模式规范化剧本的 Gemini 生成脚本

SHALL 提供 `normalize_drama_script.py` 脚本，使用 `gemini-3.1-pro-preview` 模型将小说原文转化为 Markdown 格式的规范化剧本和镜头预算。

#### Scenario: 首次生成规范化剧本
- **WHEN** `normalize-drama-script` subagent 调用该脚本
- **THEN** 脚本读取 source/ 小说原文，调用 gemini-3.1-pro-preview 生成结构化的规范化剧本，输出 `drafts/episode_{N}/step1_normalized_script.md` 和 `step2_shot_budget.md`

#### Scenario: 输出格式与 script_generator 兼容
- **WHEN** 规范化剧本生成完成
- **THEN** 输出的 `step1_normalized_script.md` 格式与现有 `ScriptGenerator.build_drama_prompt()` 所期望的输入格式一致，确保 `generate_script.py` 可无缝消费

#### Scenario: 脚本在 settings.json 中被放行
- **WHEN** 重构完成
- **THEN** `settings.json` 的 `permissions.allow` 中包含该脚本的 Bash 执行权限
