# AI 视频生成工作空间

---

## 重要总则

以下规则适用于整个项目的所有操作：

### 语言规范
- **回答用户必须使用中文**：所有回复、思考过程、任务清单及计划文件，均须使用中文
- **视频内容必须为中文**：所有生成的视频对话、旁白、字幕均使用中文
- **文档使用中文**：所有的 Markdown 文件均使用中文编写
- **Prompt 使用中文**：图片生成/视频生成使用的 prompt 应使用中文编写

### 视频规格
- **视频比例**：跟随内容模式自动设置，无需显式进行参数指定或包含在 prompt 中
  - 说书+画面模式：**9:16 竖屏**
  - 剧集动画模式：16:9 横屏
- **单片段/场景时长**：
  - 说书+画面模式：默认 **4 秒**（可选 6s/8s）
  - 剧集动画模式：默认 8 秒
- **图片分辨率**：1K
- **视频分辨率**：1080p
- **生成方式**：每个片段/场景独立生成，使用分镜图作为起始帧

> **关于 extend 功能**：Veo 3.1 extend 功能仅用于延长单个片段/场景，
> 每次固定 +7 秒，不适合用于串联不同镜头。不同片段/场景之间使用 ffmpeg 拼接。

### 音频规范
- **BGM 自动禁止**：通过 `negative_prompt` API 参数自动排除背景音乐

### 脚本调用
- **Skill 内部脚本**：各 skill 的可执行脚本位于 `agent_runtime_profile/.claude/skills/{skill-name}/scripts/` 目录下
- **虚拟环境**：默认已激活，脚本无需手动激活 .venv

---

## 内容模式

系统支持两种内容模式，通过 `project.json` 中的 `content_mode` 字段切换：

| 维度 | 说书+画面模式（默认） | 剧集动画模式 |
|------|----------------------|-------------|
| content_mode | `narration` | `drama` |
| 内容形式 | 严格保留小说原文，不改编 | 小说改编为剧本 |
| 数据结构 | `segments` 数组 | `scenes` 数组 |
| 默认时长 | 4 秒/片段 | 8 秒/场景 |
| 对白来源 | 后期人工配音（小说原文） | 演员对话 |
| 视频 Prompt | 仅包含角色对话（如有），无旁白 | 包含对话、旁白、音效 |
| 画面比例 | 9:16 竖屏（分镜图+视频） | 16:9 横屏 |
| 使用 Agent | `novel-to-narration-script` | `novel-to-storyboard-script` |

### 说书+画面模式（默认）

- **保留原文**：不改编、不删减、不添加小说原文内容
- **片段拆分**：按朗读节奏拆分为约 4 秒的片段
- **视觉设计**：为每个片段设计画面（9:16 竖屏）
- **人工配音**：原文旁白由后期人工配音，不写入视频 Prompt
- **对话保留**：仅当原文有角色对话时，将对话写入视频 Prompt

### 剧集动画模式

- **剧本改编**：将小说改编为剧本形式
- **场景设计**：每个场景默认 8 秒（16:9 横屏）
- **完整音频**：视频包含对话、旁白、音效

---

## 项目结构

- `projects/{项目名}` - 视频项目的工作空间
- `lib/` - 共享 Python 库（Gemini API 封装、项目管理）
- `agent_runtime_profile/.claude/skills/` - 可用的 skills

## 架构：编排 Skill + 聚焦 Subagent

```
主 Agent（编排层 — 极轻量）
  │  只持有：项目状态摘要 + 用户对话历史
  │  职责：状态检测、流程决策、用户确认、dispatch subagent
  │
  ├─ dispatch → analyze-characters-clues     全局角色/线索提取
  ├─ dispatch → split-narration-segments     说书模式片段拆分
  ├─ dispatch → normalize-drama-script       剧集模式规范化剧本
  ├─ dispatch → create-episode-script        JSON 剧本生成（预加载 generate-script skill）
  └─ dispatch → general-purpose subagent     资产生成（调用脚本）
```

### Skill/Agent 边界原则

| 类型 | 用途 | 示例 |
|------|------|------|
| **Subagent（聚焦任务）** | 需要大量上下文或推理分析 → 保护主 agent context | analyze-characters-clues、split-narration-segments |
| **Skill（在 subagent 内调用）** | 确定性脚本执行 → API 调用、文件生成 | generate-script、generate-characters |
| **主 Agent 直接操作** | 仅限轻量操作 | 读项目状态、简单文件操作、用户交互 |

### 关键约束

- **Subagent 不能 spawn subagent**：多步工作流只能通过主 agent 链式 dispatch
- **小说原文不进入主 agent**：由 subagent 自行读取，主 agent 只传文件路径
- **每个 subagent 一个聚焦任务**：完成即返回，不在内部做多步用户确认

## 可用 Skills

| Skill | 触发命令 | 功能 |
|-------|---------|------|
| manga-workflow | `/manga-workflow` | 编排 skill：状态检测 + subagent dispatch + 用户确认 |
| manage-project | — | 项目管理工具集：分集切分（peek+split）、角色/线索批量写入 |
| generate-script | — | 使用 Gemini 生成 JSON 剧本（由 subagent 调用） |
| generate-characters | `/generate-characters` | 生成角色设计图 |
| generate-clues | `/generate-clues` | 生成线索设计图 |
| generate-storyboard | `/generate-storyboard` | 生成分镜图片 |
| generate-video | `/generate-video` | 生成视频 |
| compose-video | `/compose-video` | 后期处理 |

## 快速开始

新用户请使用 `/manga-workflow` 开始完整的视频创作流程。

## 工作流程概览

`/manga-workflow` 编排 skill 按以下阶段自动推进（每个阶段完成后等待用户确认）：

1. **项目设置**：创建项目、上传小说、生成项目概述
2. **全局角色/线索设计** → dispatch `analyze-characters-clues` subagent
3. **分集规划** → 主 agent 直接执行 peek+split 切分（manage-project 工具集）
4. **单集预处理** → dispatch `split-narration-segments`（narration）或 `normalize-drama-script`（drama）
5. **JSON 剧本生成** → dispatch `create-episode-script` subagent
6. **角色设计** → dispatch 资产生成 subagent（调用 generate_character.py）
7. **线索设计** → dispatch 资产生成 subagent（调用 generate_clue.py）
8. **分镜图生成** → dispatch 资产生成 subagent（调用 generate_storyboard.py）
9. **视频生成** → dispatch 资产生成 subagent（调用 generate_video.py）
10. **最终合成** → dispatch 资产生成 subagent（调用 compose_video.py）

工作流支持**灵活入口**：状态检测自动定位到第一个未完成的阶段，支持中断后恢复。

## 关键原则

- **角色一致性**：每个场景都使用分镜图作为起始帧，确保角色形象一致
- **线索一致性**：重要物品和环境元素通过 `clues` 机制固化，确保跨场景一致
- **分镜连贯性**：使用 segment_break 标记场景切换点，后期可添加转场效果
- **质量控制**：每个场景生成后检查质量，可单独重新生成不满意的场景

## 项目目录结构

```
projects/{项目名}/
├── project.json       # 项目元数据（角色、线索、剧集、风格）
├── source/            # 原始小说内容
├── scripts/           # 分镜剧本 (JSON)
├── characters/        # 角色设计图
├── clues/             # 线索设计图
├── storyboards/       # 分镜图片
├── videos/            # 生成的视频
└── output/            # 最终输出
```

### project.json 核心字段

- `title`、`content_mode`（`narration`/`drama`）、`style`、`style_description`
- `overview`：项目概述（synopsis、genre、theme、world_setting）
- `episodes`：剧集核心元数据（episode、title、script_file）
- `characters`：角色完整定义（description、character_sheet、voice_style）
- `clues`：线索完整定义（type、description、importance、clue_sheet）

### 数据分层原则

- 角色/线索的完整定义**只存储在 project.json**，剧本中仅引用名称
- `scenes_count`、`status`、`progress` 等统计字段由 StatusCalculator **读时计算**，不存储
- 剧集元数据（episode/title/script_file）在剧本保存时**写时同步**
