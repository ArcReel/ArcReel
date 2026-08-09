---
name: manage-project
description: 项目管理工具集。使用场景：新增/修改角色/场景/道具到 project.json（经 patch_project 工具，按 table+name upsert）、级联重命名资产（rename_asset 工具）、写顶层 settings 字段、编辑项目概述 overview，以及查询视频模型能力（get_video_capabilities）。分集规划不在本 skill：走 mcp__arcreel__plan_episodes / reset_episode_planning 服务端工具。
user-invocable: false
---

# 项目管理工具集

提供 project.json 的角色/场景/道具批量写入、项目级 settings 与项目概述编辑，以及视频模型能力查询。

## 工具一览

| 工具 | 功能 | 调用者 |
|------|------|--------|
| `mcp__arcreel__patch_project`（SDK tool） | 新增/修改 project.json 的角色/场景/道具（按 table+name upsert）、顶层 settings 字段或项目概述（overview 分支） | subagent / 主 agent |
| `mcp__arcreel__rename_asset`（SDK tool） | 级联重命名资产：一次改齐资产表 key、全部剧集剧本与 step1 草稿的名称引用（引用数组 / speaker / `@[名称]` mention）及关联文件与版本历史 | subagent / 主 agent |
| `mcp__arcreel__get_video_capabilities`（SDK tool） | 查视频模型能力（model 粒度，按项目唯一 generation_mode 解析，全项目同一口径，无需指定剧集） | **subagent**（执行任务时自行查询） |

> 分集规划（拆集/调整）由服务端工具 `mcp__arcreel__plan_episodes` / `mcp__arcreel__reset_episode_planning` 完成，调整已规划内容走「重置 + 重新规划」，流程见 manga-workflow 阶段 2。

## 角色/场景/道具写入

经 `mcp__arcreel__patch_project` 工具写入（项目名由 session 绑定，无需传参）。按 table 分别调用，
每个 entry 以 name 为键 upsert：name 不存在则新增、存在则合并改字段。**修订已有资产描述需用户显式
意图驱动**（避免静默覆盖人工编辑过的字段）;新增提取由 analyze-assets subagent 负责并默认 skip 已存在的。

```text
mcp__arcreel__patch_project({"table": "characters", "entries": {"角色名": {"description": "...", "voice_style": "..."}}})
mcp__arcreel__patch_project({"table": "scenes", "entries": {"场景名": {"description": "..."}}})
mcp__arcreel__patch_project({"table": "props", "entries": {"道具名": {"description": "..."}}})
mcp__arcreel__patch_project({"settings": {"episode_target_units": 1000}})
mcp__arcreel__patch_project({"settings": {"source_language": "en"}})
mcp__arcreel__patch_project({"settings": {"narration_voice": "Ethan", "narration_speed": 1.2}})
mcp__arcreel__patch_project({"overview": {"genre": "悬疑", "theme": "复仇与救赎"}})
```

**三种调用形态三选一**：传 `{"table", "entries"}` 走资产 upsert，传 `{"settings"}` 走顶层字段写入，
传 `{"overview"}` 走项目概述编辑；同时给出多个或都不给会被拒。`settings` 白名单字段：

- `episode_target_units`：`int >= 1` 设置 / `null` 清除。每集目标体量（按 `source_language` 解读为阅读单位），分集规划工具按它把握每集切分体量
- `source_language`：`"zh" / "en" / "vi"` 设置 / `null` 清除。优先级：**用户显式配置 > 自动推断**——用户明确指定语言时即可写入（不限于 overview 跳过或失败的场景）；无用户显式确认时不要自行猜测写入，正常路径由 overview 生成自动落盘。发现显式配置与自动推断 / 源文实际语言不一致时，提醒用户（WARN）并按显式配置继续，不阻塞流程
- `brief`：字符串设置 / `null` 清除。创作诉求短文本，仅广告/短片项目（`content_mode=ad`）可写，其他项目类型写入会被拒
- `planning_window_chars`：`int >= 1` 设置 / `null` 清除回内部默认。分集规划单批读取的源文窗口字符数
- `planning_max_episodes`：`int >= 1` 设置 / `null` 清除回内部默认。分集规划单批最多产出的集数
- `narration_voice`：非空字符串（音色 id 照供应商文档）设置 / `null` 清除。项目级旁白音色覆盖，优先于全局设置生效，只影响当前项目
- `narration_speed`：正的有限数值（如 `1.2`）设置 / `null` 清除。项目级旁白语速覆盖，优先于全局设置生效，只影响当前项目

`generation_mode`、`grid_storyboard` 不在白名单内，`patch_project` 会拒绝写入：`generation_mode` 项目创建后不可更改，用户要求改生成方式（storyboard ↔ reference_video）时明确告知不可更改、无绕过方式；`grid_storyboard` 由用户在 Web 设置页开关，用户要求改宫格装配时指引其前往设置页操作，并告知开关只影响后续生成——已生成的分镜图不会自动失效，要按新装配方式出图须显式重新生成对应分镜。

`overview` 白名单字段：`synopsis` / `genre` / `theme` / `world_setting`，**merge 语义**（只改传入字段、
概述不存在时创建）。**修订概述需用户显式意图驱动**（避免静默覆盖人工编辑过的字段）。

工具返回会区分**新增 N 个 / 合并改字段 N 个**,并显式列出被忽略的字段（``reference_image`` /
``character_sheet`` 等系统管理字段、``type`` / ``importance`` 等已废弃字段）。结构非法（如缺
description）时不落盘并返回 `is_error: true`。
**严禁**用 Write/Edit/Bash 直接改 `project.json`——改字段走 patch_project 工具，改资产名走 rename_asset 工具。
`patch_project` 按 name upsert，用它改名只会「新名新建 + 旧名残留」且不更新任何引用。

## 查视频模型能力

通过 MCP 工具查询（项目名由 session 绑定，无需传参）：

```text
mcp__arcreel__get_video_capabilities({})
```

生成路线由项目唯一决定，无集级覆盖，能力查询全项目同一口径，不接受 / 不需要 `episode` 参数。

**返回**：JSON 文本，含 `provider_id` / `model` / `supported_durations[]` / `max_duration` / `max_reference_images` / `source` / `default_duration` / `content_mode` / `generation_mode`；narration / drama 的参考生视频项目另含 `reference_unit_durations`（`with_references` / `without_references` 两套生效档位，按 unit 有无 `@` 引用分别适用）；**ad 项目不返回该字段**——ad 的 unit 是从 `shots[]` 派生的轻量索引，镜头时长不受档位枚举管辖（规则见 `manga-workflow/SKILL.ad.md`），不要等待该字段、也不要照档位重排 ad 镜头时长。

**用途**：所有 generation_mode（storyboard / reference_video）的预处理 subagent 在执行时自查，用于决定单片段 / unit 时长。**决策优先级**（高到低）：硬约束（storyboard 时长必须取自 `supported_durations`；narration / drama 的 reference_video unit 时长必须取自该 unit 引用状态对应的 `reference_unit_durations` 档位；ad 的镜头时长按 `SKILL.ad.md` 的自由整数规则）> `default_duration` 偏好（非 null 时作默认值）> 内容需要（reference_video 按该 unit 内容实际需要的长度取档；narration / drama 长句、复杂画面可取更长值）。装不下时重拆 unit，不违约时长。

**错误**：项目未找到或模型能力无法解析时返回 `is_error: true`，文本中包含原因。
