---
name: manage-project
description: 项目管理工具集。使用场景：新增/修改角色/场景/道具到 project.json（经 patch_project 工具，按 table+name upsert）、写顶层 settings 字段、编辑项目概述 overview，以及查询视频模型能力（get_video_capabilities）。分集规划不在本 skill：走 mcp__arcreel__plan_episodes / replan_episodes 服务端工具。
user-invocable: false
---

# 项目管理工具集

提供 project.json 的角色/场景/道具批量写入、项目级 settings 与项目概述编辑，以及视频模型能力查询。

## 工具一览

| 工具 | 功能 | 调用者 |
|------|------|--------|
| `mcp__arcreel__patch_project`（SDK tool） | 新增/修改 project.json 的角色/场景/道具（按 table+name upsert）、顶层 settings 字段或项目概述（overview 分支） | subagent / 主 agent |
| `mcp__arcreel__get_video_capabilities`（SDK tool） | 查当前项目视频模型能力（model 粒度，所有生成模式通用） | **subagent**（执行任务时自行查询） |

> 分集规划（拆集/重排）由服务端工具 `mcp__arcreel__plan_episodes` / `mcp__arcreel__replan_episodes` 完成，流程见 manga-workflow 阶段 2。

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
mcp__arcreel__patch_project({"overview": {"genre": "悬疑", "theme": "复仇与救赎"}})
```

**三种调用形态三选一**：传 `{"table", "entries"}` 走资产 upsert，传 `{"settings"}` 走顶层字段写入，
传 `{"overview"}` 走项目概述编辑；同时给出多个或都不给会被拒。`settings` 白名单字段：

- `episode_target_units`：`int >= 1` 设置 / `null` 清除。每集目标体量（按 `source_language` 解读为阅读单位），分集规划工具按它把握每集切分体量
- `source_language`：`"zh" / "en" / "vi"` 设置 / `null` 清除。优先级：**用户显式配置 > 自动推断**——用户明确指定语言时即可写入（不限于 overview 跳过或失败的场景）；无用户显式确认时不要自行猜测写入，正常路径由 overview 生成自动落盘。发现显式配置与自动推断 / 源文实际语言不一致时，提醒用户（WARN）并按显式配置继续，不阻塞流程
- `planning_window_chars`：`int >= 1` 设置 / `null` 清除回内部默认。分集规划单批读取的源文窗口字符数
- `planning_max_episodes`：`int >= 1` 设置 / `null` 清除回内部默认。分集规划单批最多产出的集数

`overview` 白名单字段：`synopsis` / `genre` / `theme` / `world_setting`，**merge 语义**（只改传入字段、
概述不存在时创建）。**修订概述需用户显式意图驱动**（避免静默覆盖人工编辑过的字段）。

工具返回会区分**新增 N 个 / 合并改字段 N 个**,并显式列出被忽略的字段（``reference_image`` /
``character_sheet`` 等系统管理字段、``type`` / ``importance`` 等已废弃字段）。结构非法（如缺
description）时不落盘并返回 `is_error: true`。
**严禁**用 Write/Edit/Bash 直接改 `project.json`——只能走 patch_project 工具。

## 查视频模型能力

通过 MCP 工具查询（项目名由 session 绑定，无需传参）：

```text
mcp__arcreel__get_video_capabilities({})
```

**返回**：JSON 文本，含 `provider_id` / `model` / `supported_durations[]` / `max_duration` / `max_reference_images` / `source` / `default_duration` / `content_mode` / `generation_mode`。

**用途**：所有 generation_mode（storyboard / grid / reference_video）的预处理 subagent 在执行时自查，用于决定单片段 / shot 时长。**决策优先级**（高到低）：硬约束（时长必须取自 `supported_durations`；reference_video 的 unit 总时长 ≤ `max_duration`）> `default_duration` 偏好（非 null 时作默认值）> 打包效率 / 内容需要（reference_video 组合 shot 贴近 `max_duration`；narration / drama 长句、复杂画面可取更长值）。超限时重拆 unit，不违约时长。

**错误**：项目未找到或模型能力无法解析时返回 `is_error: true`，文本中包含原因。
