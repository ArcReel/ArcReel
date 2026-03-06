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

## 可用 Skills

| Skill | 触发命令 | 功能 |
|-------|---------|------|
| generate-characters | `/generate-characters` | 生成人物设计图 |
| generate-clues | `/generate-clues` | 生成线索设计图（重要物品/环境） |
| generate-storyboard | `/generate-storyboard` | 生成分镜图片 |
| generate-video | `/generate-video` | 生成连续视频（推荐）或独立视频 |
| compose-video | `/compose-video` | 后期处理（添加 BGM、片头片尾） |
| manga-workflow | `/manga-workflow` | 完整工作流程 |

## 快速开始

新用户请使用 `/manga-workflow` 开始完整的视频创作流程。

## 工作流程（说书+画面模式）

1. **准备小说**：将小说文本放入 `projects/{项目名}/source/`
2. **项目概述**：上传源文件后系统自动生成项目概述（synopsis、genre、theme、world_setting），供后续 Agent 参考
3. **创建项目**：设置 `content_mode: "narration"`（默认）和 `style`
4. **生成剧本**：系统调用 `novel-to-narration-script` agent 执行三步流程：
   - Step 1: 拆分片段（按朗读节奏，默认 4 秒/片段，含 segment_break 标记）
   - Step 2: 角色表/线索表（生成参考表并写入 project.json）
   - Step 3: 生成 JSON（使用 segments 结构）
5. **人物生成**：`/generate-characters` 生成人物设计图（3:4 竖版）
6. **线索生成**：`/generate-clues` 生成线索设计图（16:9 横屏）
7. **分镜图片**：`/generate-storyboard` 直接生成分镜图
   - 直接生成单独场景图（**9:16 竖屏**）
   - 根据 script.json 定义自动使用 character_sheet 和 clue_sheet 作为参考图保持一致性
8. **视频生成**：`/generate-video` 生成视频
   - **9:16 竖屏**格式
   - 每个片段独立生成，使用分镜图作为起始帧
   - 视频 Prompt 仅包含角色对话（如有），不包含旁白
   - 支持断点续传

每个步骤完成后需要等待用户确认，确认后再继续下一步。

## 工作流程（剧集动画模式）

如需使用剧集动画模式，在 `project.json` 中设置 `content_mode: "drama"`：

1. **准备小说**：将小说文本放入 `projects/{项目名}/source/`
2. **项目概述**：上传源文件后系统自动生成项目概述（synopsis、genre、theme、world_setting），供后续 Agent 参考
3. **生成剧本**：系统调用 `novel-to-storyboard-script` agent,agent 进行人物/线索设计后将小说转为分镜剧本
4. **人物生成**：`/generate-characters` 生成人物设计图
5. **线索生成**：`/generate-clues` 生成线索设计图
6. **分镜图片**：`/generate-storyboard` 直接生成分镜图（16:9 横屏）
7. **视频生成**：`/generate-video` 生成视频（16:9 横屏）

## 关键原则

- **人物一致性**：每个场景都使用分镜图作为起始帧，确保人物形象一致
- **线索一致性**：重要物品和环境元素通过 `clues` 机制固化，确保跨场景一致
- **分镜连贯性**：使用 segment_break 标记场景切换点，后期可添加转场效果
- **质量控制**：每个场景生成后检查质量，可单独重新生成不满意的场景

## 环境要求

- Python 3.10+
- Gemini API 密钥 或 Vertex AI 配置（通过 WebUI 配置页设置）
- ffmpeg（用于视频后期处理）

## 项目目录结构

```
projects/{项目名}/
├── project.json       # 项目元数据（人物、线索、剧集、风格）
├── source/            # 原始小说内容
├── scripts/           # 分镜剧本 (JSON)
├── characters/        # 人物设计图
├── clues/             # 线索设计图
├── storyboards/       # 分镜图片
├── videos/            # 生成的视频
└── output/            # 最终输出
```

### project.json 核心字段

- `title`、`content_mode`（`narration`/`drama`）、`style`、`style_description`
- `overview`：项目概述（synopsis、genre、theme、world_setting）
- `episodes`：剧集核心元数据（episode、title、script_file）
- `characters`：人物完整定义（description、character_sheet、voice_style）
- `clues`：线索完整定义（type、description、importance、clue_sheet）

### 数据分层原则

- 角色/线索的完整定义**只存储在 project.json**，剧本中仅引用名称
- `scenes_count`、`status`、`progress` 等统计字段由 StatusCalculator **读时计算**，不存储
- 剧集元数据（episode/title/script_file）在剧本保存时**写时同步**
