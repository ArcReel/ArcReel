# AI 视频生成工作空间
<!-- mode: narration -->

---

## 重要总则

以下规则适用于整个项目的所有操作：

### 视频规格
- **视频比例**：由项目 `aspect_ratio` 配置决定，无需在 prompt 中指定
- **单片段/场景时长**：由视频模型能力和项目 `default_duration` 配置决定
  - storyboard / grid 模式：由项目 `default_duration` 决定
  - reference_video 模式：由所选视频模型的 `supported_durations` 决定；subagent 运行时通过 `mcp__arcreel__get_video_capabilities` 工具自查真值
- **图片分辨率**：1K
- **视频分辨率**：1080p
- **生成方式**：每个片段/场景独立生成，使用分镜图作为起始帧

> **关于 extend 功能**：Veo 3.1 extend 功能仅用于延长单个片段/场景，
> 每次固定 +7 秒，不适合用于串联不同镜头。不同片段/场景之间使用 ffmpeg 拼接。

### 音频规范
- **BGM 自动禁止**：在视频 prompt 末尾统一追加"禁止出现：BGM、文字字幕、水印"

### 工具调用

- **业务入队 / 文本生成 / 能力查询**：统一走 `mcp__arcreel__*` 系列 SDK in-process MCP tool（角色/场景/道具/分镜/视频/宫格/集脚本/规范化剧本/视频能力查询）。它们跑在 server 主进程，不受 sandbox 网络白名单约束，agent 直接以 tool 形式调用。
- **编辑项目 JSON**：修改剧本（`scripts/*.json`）或角色/场景/道具（`project.json`）**一律走 `mcp__arcreel__*` 编辑工具**——剧本改字段用 `patch_episode_script`，增/删/拆分镜用 `insert_segment` / `remove_segment` / `split_segment`，角色/场景/道具用 `patch_project`。**严禁**用 Write / Edit / Bash 直改这两类文件（已被 sandbox `denyWrite` 与 PreToolUse hook 双层拒绝）。**改 prompt 必重生**：用 `patch_episode_script` 改了某分镜的 `image_prompt` / `video_prompt` 后，工具不会自动作废旧图/视频，必须紧接着调对应生成工具重新生成该分镜，否则会留下「新 prompt + 旧画面」的陈旧。
- **Bash 用途**：仅供通用排查与文件浏览（`ls / cat / jq / python / curl` 等），以及 `manage-project` / `compose-video` 这两个 skill 内还保留的 Python 脚本。
- **敏感文件保护**：`.env` / `vertex_keys/` / `.system_config.json*` / `.arcreel.db*` / `.claude/settings.json` 由 sandbox profile（`filesystem.denyRead`）内核级拒绝读取，并由 PreToolUse 文件访问 hook 双重防御；代码文件（.py/.js/.ts/.tsx/.sh/.yaml/.yml/.toml）受运行时 hook 阻止写入。

### 路径规范

agent session 的当前工作目录（cwd）已绑定到当前项目根，**所有工具参数中的路径必须遵循以下规则**：

- **Read / Edit / Write / Glob / Grep**：`file_path` 使用**绝对路径**
- **Bash 调用 skill 脚本**：使用**相对项目根 cwd** 的路径，例如：
  - ✅ `source/episode_1.txt`、`drafts/episode_1/step1_segments.md`、`source/_remaining.txt`
  - ❌ `projects/{项目名}/source/episode_1.txt`（双前缀，占位符替换或拼接出错就会落到 projects 根）
- **严禁**在工具参数中出现 `projects/{...}/` 前缀；该前缀仅用于文档说明项目目录结构，**不可直接作为参数传给任何工具**
- skill 脚本内部已加 cwd 校验，cwd 漂离当前项目目录时会直接拒绝执行
- **关于 agent.md / SKILL.md 中的相对形式**：subagent 指引（如「读取 `project.json`」、「读取 `source/episode_{N}.txt`」）里出现的相对路径是**项目内位置说明**，并非可直接传给工具的 `file_path` 值。调用 Read/Edit/Write/Glob/Grep 时仍按本节规则用 session cwd 拼成绝对路径再传参

---

## 内容模式

本项目为**说书+画面模式**（narration）。剧本数据结构为 `segments[]`，每个片段对应一段朗读 + 一张分镜画面。

> 生成模式（storyboard / grid / reference_video）通过 `project.json` 的 `generation_mode` 字段配置，与内容模式独立。详细规格见 `.claude/references/generation-modes.md`。

---

## 生成模式

系统支持三种**生成模式**（`generation_mode`），通过 `project.json` 顶层字段 + 集级 `episodes[i].generation_mode` 指定：

| generation_mode | 名称（UI） | 数据主结构 | 视觉参考来源 |
|---|---|---|---|
| `storyboard`（默认） | 图生视频 | `segments[]` 或 `scenes[]` + 分镜图 | 每片段一张分镜图作起始帧 |
| `grid` | 宫格生视频 | `segments[]` 或 `scenes[]` + 宫格分组 | 宫格图切块 |
| `reference_video` | 参考生视频 | `video_units[]` | 角色/场景/道具 sheet 图作为参考 |

解析规则：`effective_mode(project, episode) = episode.generation_mode or project.generation_mode or "storyboard"`。

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
  │  职责：状态检测、流程决策、用户确认、dispatch subagent
  │
  ├─ dispatch → analyze-assets               全局角色/场景/道具提取
  ├─ dispatch → split-narration-segments     说书模式片段拆分
  ├─ dispatch → normalize-drama-script       剧集模式规范化剧本
  ├─ dispatch → split-reference-video-units  参考模式 video_unit 拆分
  ├─ dispatch → create-episode-script        JSON 剧本生成（预加载 generate-script skill）
  └─ dispatch → generate-assets              资产生成（角色/场景/道具/分镜/视频）
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

- **禁止编写代码**：不得创建或修改任何代码文件（.py/.js/.sh 等），数据处理走 `mcp__arcreel__*` 工具或 `manage-project` / `compose-video` 的现有脚本
- **代码 bug 上报**：如果明确判断 MCP 工具或 skill 脚本出现的是代码 bug（而非参数或环境问题），向用户报告错误并建议反馈给开发者

## 可用 Skills

| Skill | 触发命令 | 功能 |
|-------|---------|------|
| manga-workflow | `/manga-workflow` | 编排 skill：状态检测 + subagent dispatch + 用户确认 |
| manage-project | — | 项目管理工具集：分集切分（peek+split）、角色/场景/道具批量写入 |
| generate-script | — | 调用项目配置的文本模型生成 JSON 剧本（由 subagent 调用） |
| generate-assets | `/generate-assets` | 统一资产生成：可指定 `type=character\|scene\|prop`，省略则三类并行 |
| generate-storyboard | `/generate-storyboard` | 生成分镜图片（storyboard 模式） |
| generate-grid | `/generate-grid` | 生成宫格分镜图（grid 模式：按 segment_break 分组的链式宫格） |
| generate-video | `/generate-video` | 生成视频 |
| compose-video | `/compose-video` | 视频后期合成（BGM、片头片尾、多集拼接，ffmpeg） |

## 快速开始

新用户请使用 `/manga-workflow` 开始完整的视频创作流程。

## 工作流程概览

`/manga-workflow` 编排 skill 按以下阶段自动推进（每个阶段完成后等待用户确认）：

1. **项目设置**：创建项目（创建时确定 `content_mode`，之后不可变）、选择 `generation_mode`、上传小说、生成项目概述
2. **全局角色/场景/道具提取** → dispatch `analyze-assets` subagent
3. **分集规划** → 主 agent 直接执行 peek+split 切分（manage-project 工具集）
4. **单集预处理** → 按 `effective_mode` 选：
   - reference_video → `split-reference-video-units`
   - narration → `split-narration-segments`
   - drama → `normalize-drama-script`
5. **JSON 剧本生成** → dispatch `create-episode-script` subagent
6. **资产设计（character/scene/prop 三类并行）** → dispatch `generate-assets` subagent
7. **分镜图生成**：仅 `storyboard` / `grid` 模式；`reference_video` 跳过 → dispatch `generate-assets` subagent
8. **视频生成** → dispatch `generate-assets` subagent（脚本自动按 video_units/segments/scenes 分派）

工作流支持**灵活入口**：状态检测自动定位到第一个未完成的阶段，支持中断后恢复。
视频生成完成后，用户可在 Web 端导出为剪映草稿。

## 关键原则

- **角色一致性**：每个场景都使用分镜图作为起始帧，确保角色形象一致
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
├── storyboards/       # 分镜图片（storyboard / grid 模式）
├── grids/             # 宫格图（grid 模式）
├── videos/            # 生成的视频片段（storyboard / grid 模式）
├── reference_videos/  # 生成的 video_unit（reference_video 模式）
├── thumbnails/        # 首帧缩略图
└── output/            # 最终输出
```

### project.json 核心字段

- `schema_version`：项目数据格式版本（当前 1）
- `title`、`content_mode`（`narration`/`drama`）、`generation_mode`（`storyboard`/`grid`/`reference_video`）、`style`、`style_description`
- `overview`：项目概述（synopsis、genre、theme、world_setting）
- `episodes`：剧集核心元数据（episode、title、script_file、可选 `generation_mode` 覆盖）
- `characters`：角色完整定义（description、voice_style、character_sheet）
- `scenes`：场景完整定义（description、scene_sheet）
- `props`：道具完整定义（description、prop_sheet）

### 数据分层原则

- 角色/场景/道具的完整定义**只存储在 project.json**，剧本中仅引用名称
- `scenes_count`、`status`、`progress` 等统计字段由 StatusCalculator **读时计算**，不存储
- 剧集元数据（episode/title/script_file）在剧本保存时**写时同步**
