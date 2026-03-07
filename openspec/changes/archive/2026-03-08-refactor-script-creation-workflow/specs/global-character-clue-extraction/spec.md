## ADDED Requirements

### Requirement: 角色/线索提取须支持全书分析模式

`analyze-characters-clues` subagent SHALL 支持分析整部小说并一次性提取所有角色和线索。

#### Scenario: 分析整部小说
- **WHEN** subagent 被 dispatch 且未指定分析范围
- **THEN** subagent 读取 `projects/{project_name}/source/` 下的所有小说文本，提取全部角色和线索，写入 project.json

#### Scenario: 分析指定章节范围
- **WHEN** subagent 被 dispatch 且指定了分析范围（如"第1-3章"或"某个文件"）
- **THEN** subagent 只分析指定范围的文本，提取该范围内的角色和线索

### Requirement: 角色/线索提取须支持增量追加模式

当 project.json 中已有角色/线索时，subagent SHALL 对比现有数据，只追加新发现的角色和线索，不覆盖已有定义。

#### Scenario: 已有角色列表时追加新角色
- **WHEN** project.json 中已有 5 个角色定义，subagent 分析后发现 3 个新角色
- **THEN** subagent 只将 3 个新角色追加到 project.json，保留原有 5 个角色不变

#### Scenario: 已有角色的描述不被覆盖
- **WHEN** project.json 中某角色已有手动修改过的 description 或 character_sheet
- **THEN** subagent 不覆盖该角色的已有数据，仅在返回摘要中标注"已存在，跳过"

### Requirement: 角色提取结果须符合图像生成规范

提取的角色描述 SHALL 仅包含可直接用于图像生成的视觉信息。

#### Scenario: 角色描述仅含视觉要素
- **WHEN** subagent 提取角色信息
- **THEN** description 字段包含外貌要点、服装、标志物、色彩关键词、参考风格，不包含性格描述、人物关系、剧情背景等非视觉信息

#### Scenario: voice_style 单独记录
- **WHEN** 小说中有角色声音/语气描述
- **THEN** subagent 将声音信息记录在 voice_style 字段（用于后期配音参考），与视觉描述分离

### Requirement: 线索提取须区分类型和重要性

提取的线索 SHALL 标记类型（location/prop）和重要性（major/minor）。

#### Scenario: 场景类线索标记为 location
- **WHEN** 线索为环境/场景（如"竹林深处"、"客栈大堂"）
- **THEN** 线索 type 标记为 "location"，描述包含空间结构、光线氛围

#### Scenario: 道具类线索标记为 prop
- **WHEN** 线索为物品/道具（如"玉佩"、"信件"）
- **THEN** 线索 type 标记为 "prop"，描述包含尺寸参考、材质、外观细节

#### Scenario: 重要线索标记为 major
- **WHEN** 线索在剧情中反复出现或具有关键作用
- **THEN** 线索 importance 标记为 "major"（后续将生成设计图）

#### Scenario: 次要线索标记为 minor
- **WHEN** 线索仅偶尔出现或为背景装饰
- **THEN** 线索 importance 标记为 "minor"（仅保留描述，不生成设计图）

### Requirement: 提取结果须通过数据验证

subagent 写入 project.json 后 SHALL 调用数据验证确保完整性。

#### Scenario: 调用 validate_project 验证
- **WHEN** subagent 完成角色/线索写入
- **THEN** subagent 调用 `validate_project(project_name)` 验证 project.json 结构和引用完整性

#### Scenario: 验证失败时修复
- **WHEN** validate_project 返回验证失败
- **THEN** subagent 根据错误信息修复数据，重新验证直到通过

### Requirement: subagent 须返回结构化摘要

`analyze-characters-clues` subagent 返回给主 agent 的结果 SHALL 为精炼的结构化摘要，不包含原始小说文本。

#### Scenario: 返回角色摘要
- **WHEN** subagent 完成角色提取
- **THEN** 返回内容包含：新增角色数量、角色名称列表、每个角色的一句话描述

#### Scenario: 返回线索摘要
- **WHEN** subagent 完成线索提取
- **THEN** 返回内容包含：新增线索数量、major/minor 分布、线索名称和类型列表
