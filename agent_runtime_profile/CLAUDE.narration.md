# AI 视频生成工作空间
<!-- mode: narration -->

---

## 重要总则

以下规则适用于整个项目的所有操作：

### 视频规格
- **视频比例**：由项目 `aspect_ratio` 配置决定，无需在 prompt 中指定
- **单片段/场景时长**：由视频模型能力和项目 `default_duration` 配置决定
  - storyboard 模式（含 `grid_storyboard=true`）：取值必须在所选视频模型的 `supported_durations` 内，项目 `default_duration` 非 null 时作默认偏好
  - reference_video 模式：unit 时长必须取该 unit **引用状态对应**的生效档位（`reference_unit_durations.with_references` / `.without_references`）
  - 两者的真值均由 subagent 运行时通过 `mcp__arcreel__get_video_capabilities` 工具自查；该工具返回的 `supported_durations` 是型号声明的全集，**未**施加「分辨率↔时长」「参考图↔时长」两条联动约束，生成工具会按项目分辨率再收窄一次。手工改 step1 时长后若入队被拒，按错误提示取收窄后的档位，不要反复重试原值
- **图片分辨率**：1K
- **视频分辨率**：1080p
- **生成方式**：按 `generation_mode` 分两路——storyboard 模式每个片段/场景独立生成、以分镜图作起始帧（`grid_storyboard=true` 时起始帧来自宫格切块）；reference_video 模式按 video_unit 直出、以资产 sheet 图作 `reference_images`，无分镜图

> **关于 extend 功能**：Veo 3.1 extend 功能仅用于延长单个片段/场景，
> 每次固定 +7 秒，不适合用于串联不同镜头。不同片段/场景之间使用 ffmpeg 拼接。

### 音频规范
- **BGM 自动禁止**：生成端已在视频 prompt 末尾自动追加「禁止出现：BGM、文字字幕、水印」，无需手动追加，video_prompt 里也不要描述 BGM / 配乐

### 视频 prompt 措辞

- **避开任务类型触发词**：`video_prompt` 里不要用「增加 / 删除 / 去掉 / 修改 / 替换 / 改成 / 延长 / 续写」这类祈使动词。部分模型（如 Seedance 2.5）按 prompt 措辞判定任务类型，带这些词会把参考生视频误判成视频编辑或视频延长，而误判在异步生成阶段才报错——任务已排队、已计费。改成直接描述目标画面本身：不写「把外套改成红色」，写「她穿着红色外套」

### 工具调用

- **业务入队 / 文本生成 / 能力查询**：统一走 `mcp__arcreel__*` 系列 SDK in-process MCP tool（角色/场景/道具/分镜/视频/宫格/图片编辑/集脚本/规范化剧本/说书片段拆分/参考视频单元拆分/分集规划与重置/视频能力查询）。它们跑在 server 主进程，不受 sandbox 网络白名单约束，agent 直接以 tool 形式调用。
- **图片编辑 vs 重新生成**：审核检查点用户只想改设计图/分镜图的局部（换色、去杂物、调光线等）时用 `edit_images`——保底图微调、不改 `description`/`image_prompt`；用户想推翻构图整体重来、或本来就要改 description/image_prompt 时仍用对应的 `generate_*` 工具重新生成。用户脱离生成流程直接说「把某某改一下」时也可直接调 `edit_images`，不依赖处于哪个工作流步骤。
- **编辑项目 JSON**：修改剧本（`scripts/*.json`）或角色/场景/道具（`project.json`）**一律走 `mcp__arcreel__*` 编辑工具**——批量改剧本时先调用 `get_episode_script_revision`，再把其 revision 原样作为 `patch_episode_script` 的 `expected_revision`，并传有序 `operations[]`（`update` / `insert_after` / `move_after` / `remove`）；整批先预检后原子提交，失败结果用 `operation_index` 与 field location 定位，revision 冲突时重新读取再重做。改分集标题用 `patch_episode_meta`，增/删/拆分镜的便捷工具也委托同一事务编辑器；角色/场景/道具用 `patch_project`。**严禁**用 Write / Edit / Bash 直改这两类文件（已被 sandbox `denyWrite` 与 PreToolUse hook 双层拒绝）。**改 prompt 必重生**：用 `patch_episode_script` 改了某些分镜的 `image_prompt` / `video_prompt` 后，工具不会自动作废旧图/视频，必须紧接着调对应生成工具重新生成这些分镜，否则会留下「新 prompt + 旧画面」的陈旧。
- **Bash 用途**：仅供通用排查与文件浏览（`ls / cat / jq / python / curl` 等）。
- **敏感文件保护**：`.env` / `vertex_keys/` / `.system_config.json*` / `.arcreel.db*` / `.claude/settings.json` 由 sandbox profile（`filesystem.denyRead`）内核级拒绝读取，并由 PreToolUse 文件访问 hook 双重防御；代码文件（.py/.js/.ts/.tsx/.sh/.yaml/.yml/.toml）受运行时 hook 阻止写入。

### 路径规范

agent session 的当前工作目录（cwd）已绑定到当前项目根，**所有工具参数中的路径必须遵循以下规则**：

- **Read / Edit / Write / Glob / Grep**：`file_path` 使用**绝对路径**
- **Bash 调用 skill 脚本**：使用**相对项目根 cwd** 的路径，例如：
  - ✅ `source/episode_1.txt`、`drafts/episode_1/step1_segments.json`、`scripts/episode_1.json`
  - ❌ `projects/{项目名}/source/episode_1.txt`（双前缀，占位符替换或拼接出错就会落到 projects 根）
- **严禁**在工具参数中出现 `projects/{...}/` 前缀；该前缀仅用于文档说明项目目录结构，**不可直接作为参数传给任何工具**
- skill 脚本内部已加 cwd 校验，cwd 漂离当前项目目录时会直接拒绝执行
- **关于 agent.md / SKILL.md 中的相对形式**：subagent 指引（如「读取 `project.json`」、「读取 `source/episode_{N}.txt`」）里出现的相对路径是**项目内位置说明**，并非可直接传给工具的 `file_path` 值。调用 Read/Edit/Write/Glob/Grep 时仍按本节规则用 session cwd 拼成绝对路径再传参

---

## 内容模式

本项目为**说书+画面模式**（narration）。剧本数据结构为 `segments[]`，每个片段对应一段朗读 + 一张分镜画面。

> 生成模式（storyboard / reference_video）由 `project.json` 顶层 `generation_mode` 字段唯一决定，项目创建后不可更改；与内容模式独立。详细规格见 `.claude/references/generation-modes.md`。

---

## 生成模式

系统支持两种**生成模式**（`generation_mode`），由 `project.json` 顶层字段唯一表达，创建后不可更改，不存在集级覆盖：

| generation_mode | 名称（UI） | 数据主结构 | 视觉参考来源 |
|---|---|---|---|
| `storyboard` | 图生视频 / 宫格生视频 | `segments[]` 或 `scenes[]` + 分镜图 | 每片段一张分镜图作起始帧；`grid_storyboard=true` 时改用宫格图切块 |
| `reference_video` | 参考生视频 | `video_units[]` | 角色/场景/道具 sheet 图作为参考 |

宫格不是独立生成模式：`grid_storyboard` 是仅在 `generation_mode="storyboard"` 下生效的独立布尔开关，切换宫格 UI 在设置页操作，agent 无法经工具绕过。

> 完整模式矩阵与阶段分支详见 `.claude/references/generation-modes.md`。

---

## 项目结构

- `projects/{项目名}` - 视频项目的工作空间
- `lib/` - 共享 Python 库（多供应商图像 / 视频 / 文本生成抽象层、项目管理）
- `agent_runtime_profile/.claude/skills/` - 可用的 skills

## 架构：编排 Skill + 聚焦 Subagent

```
主 Agent（编排层 — 极轻量）
  │  只持有：项目状态摘要 + 用户对话历史
  │  职责：查服务端计划、按受控动作决策、用户确认、dispatch subagent
  │
  ├─ dispatch → analyze-assets               全局角色/场景/道具提取
  ├─ dispatch → split-narration-segments     说书模式片段拆分
  ├─ dispatch → normalize-drama-script       剧集模式规范化剧本
  ├─ dispatch → split-reference-video-units  参考模式 video_unit 拆分
  ├─ dispatch → create-episode-script        JSON 剧本生成（预加载 generate-script skill）
  └─ dispatch → generate-assets              资产生成（角色/场景/道具/分镜/视频/旁白配音）
```

### Skill/Agent 边界原则

| 类型 | 用途 | 示例 |
|------|------|------|
| **Subagent（聚焦任务）** | 需要大量上下文或推理分析 → 保护主 agent context | analyze-assets、split-narration-segments |
| **Skill（在 subagent 内调用）** | 确定性脚本执行 → API 调用、文件生成 | generate-script、generate-assets |
| **主 Agent 直接操作** | 仅限轻量操作 | 读项目状态、简单文件操作、用户交互 |

### 关键约束

- **Subagent 不能 spawn subagent**：多步工作流只能通过主 agent 链式 dispatch
- **小说原文不进入主 agent**：由 subagent 自行读取，主 agent 只传文件路径
- **每个 subagent 一个聚焦任务**：完成即返回，不在内部做多步用户确认

### 职责边界

- **禁止编写代码**：不得创建或修改任何代码文件（.py/.js/.sh 等），数据处理走 `mcp__arcreel__*` 工具或 `manage-project` 的现有脚本
- **代码 bug 上报**：如果明确判断 MCP 工具或 skill 脚本出现的是代码 bug（而非参数或环境问题），向用户报告错误并建议反馈给开发者

## 可用 Skills

| Skill | 触发命令 | 功能 |
|-------|---------|------|
| video-workflow | `/video-workflow` | 编排 skill：查计划 + subagent dispatch + 用户确认 |
| manage-project | — | 项目管理工具集：角色/场景/道具批量写入、项目 settings 与概述编辑 |
| generate-script | — | 调用项目配置的文本模型生成 JSON 剧本（由 subagent 调用） |
| generate-assets | `/generate-assets` | 统一资产生成：可指定 `type=character\|scene\|prop`，省略则三类并行 |
| generate-storyboard | `/generate-storyboard` | 生成分镜图片（storyboard 模式） |
| generate-grid | `/generate-grid` | 生成宫格分镜图（`grid_storyboard=true` 时：按 segment_break 分组的链式宫格） |
| generate-video | `/generate-video` | 生成视频 |
| generate-narration-audio | `/generate-narration-audio` | 生成旁白配音（按段 TTS，只依赖剧本 novel_text） |

## 快速开始

新用户请使用 `/video-workflow` 开始完整的视频创作流程。

## 工作流程概览

`/video-workflow` 编排 skill 按服务端计划推进（每个动作完成后等待用户确认）。**步骤表不在这里，
也不在 skill 里**：调用 `mcp__arcreel__get_workflow_plan` 取回 `steps[]` 与唯一的 `next_action`，
照它路由。六种模式组合的步骤适用性、受控动作表、旁白交付、批量准入与状态轴读法见
`.claude/references/workflow-plan.md`。

需要在这里说清、不由计划表达的几条：

- 生成路线（storyboard ↔ reference_video）创建后不可更改，无绕过方式；宫格装配（`grid_storyboard`）
  由用户在设置页开关，agent 无写入权限。该开关只影响后续生成，已生成的分镜图不会自动失效，
  须显式重新生成对应分镜才会按新装配方式出图
- 分集规划的常驻偏好（如按章节对齐切分）不持久化，须经 `plan_episodes` 的 `instructions` 在**每一批
  调用上重复带上**；每集目标体量等全局性偏好经 `patch_project` 显式写入 `episode_target_units`
- 预处理中间文件被修改 / 重拆后必须重新生成剧本 JSON，剧本不会自动跟随中间文件更新
- `reference_video` **只跳过分镜图**，不跳过 audio：旁白交付选择在两条路线上都要逐次做
- 批量旁白配音由用户显式要求触发，不由 `next_action` 驱动

工作流支持**灵活入口**：计划自动定位到第一个未完成的动作，支持中断后恢复。
视频生成完成后，用户可在 Web 端导出为剪映草稿——声音归属与字幕时序由服务端 presentation 结果决定，
预览、下载与剪映草稿消费同一份；agent 不自行估算字幕时序、不静音 provider 原音、
也不替用户判断 TTS 是否必需。stale 产物照常可导出，导出不清空也不覆盖旧付费媒体。

## 关键原则

- **角色一致性**：storyboard 模式每个片段都使用分镜图作为起始帧；reference_video 模式改由 unit 引用的角色 sheet 图承担同一职责，两者都确保角色形象一致
- **场景/道具一致性**：标志性环境和关键道具通过 `scenes` / `props` 机制固化，确保跨场景视觉一致
- **分镜连贯性**：使用 segment_break 标记场景切换点，后期可添加转场效果
- **质量控制**：每个场景生成后检查质量，可单独重新生成不满意的场景

## 项目目录结构

> 下面的目录树仅为说明用途，agent session 的 cwd 已在项目根。**Bash 调用 skill 脚本**时使用相对 cwd 的路径（如 `source/`、`scripts/`）；**Read / Edit / Write / Glob / Grep** 的 `file_path` 仍按上文"路径规范"要求使用**绝对路径**。无论哪种工具都不可带 `projects/{项目名}/` 前缀。

```text
projects/{项目名}/      # ← session cwd 已在此，下面均为 cwd 内的相对路径
├── project.json       # 项目元数据（角色、场景、道具、剧集、风格）
├── source/            # 原始小说内容
├── scripts/           # 分镜剧本 (JSON)
├── drafts/            # Step 1 中间文件
├── characters/        # 角色设计图
├── scenes/            # 场景设计图
├── props/             # 道具设计图
├── storyboards/       # 分镜图片（storyboard 模式；`grid_storyboard=true` 时存宫格切割出的起始分镜图）
├── grids/             # 宫格大图（storyboard 模式且 `grid_storyboard=true`）
├── videos/            # 生成的视频片段（storyboard 模式）
├── reference_videos/  # 生成的 video_unit（reference_video 模式）
├── audio/             # 旁白音频（说书模式，首次生成时创建）
├── thumbnails/        # 首帧缩略图
└── output/            # 最终输出
```

### project.json 核心字段

- `schema_version`：项目数据格式版本（当前 1）
- `title`、`content_mode`（`narration`/`drama`）、`generation_mode`（`storyboard`/`reference_video`，创建后不可更改）、`grid_storyboard`（布尔，仅 `generation_mode="storyboard"` 下生效，由用户在设置页开关）、`style`、`style_description`
- `overview`：项目概述（synopsis、genre、theme、world_setting）
- `episodes`：分集账本（单一真相源）：episode、title、script_file，以及账本字段 `source_range`（原文范围）/ `hook`（集尾钩子）/ `outline`（drama 分集大纲）/ `ledger_status`（planned/consumed/stale）；顶层 `planning_cursor` 标记下一批规划起点。`source/episode_N.txt` 是账本的派生物，由规划工具维护，不要手工编辑或重命名
- `characters`：角色完整定义（description、voice_style、character_sheet）
- `scenes`：场景完整定义（description、scene_sheet）
- `props`：道具完整定义（description、prop_sheet）

### 数据分层原则

- 角色/场景/道具的完整定义**只存储在 project.json**，剧本中仅引用名称
- `item_count`（分镜数 / 视频单元数）、`status`、产物计数等统计字段由项目摘要**读时计算**，不存储
- 剧集元数据（episode/title/script_file）在剧本保存时**写时同步**
