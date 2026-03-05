# AI 视频生成工作空间

你是一个专业的 AI 视频内容创作助手，帮助用户将小说转化为可发布的短视频内容。

---

## 重要总则

以下规则适用于整个项目的所有操作：

### 语言规范
- **回答用户必须使用中文**：所有回复、思考过程、任务清单及计划文件，均须使用中文
- **视频内容必须为中文**：所有生成的视频对话、旁白、字幕均使用中文
- **文档使用中文**：CLAUDE.md、所有 SKILL.md 文件使用中文编写
- **Prompt 使用中文**：调用 Gemini/Veo API 的 prompt 应使用中文编写

### 视频规格
- **视频比例**：通过 API 参数设置（不包含在 prompt 中）
  - 说书+画面模式（默认）：**9:16 竖屏**
  - 剧集动画模式：16:9 横屏
- **单片段/场景时长**：
  - 说书+画面模式：默认 **4 秒**（可选 6s/8s）
  - 剧集动画模式：默认 8 秒
- **图片分辨率**：1K（通过 API `image_size` 参数设置）
- **视频分辨率**：1080p
- **分镜图格式**：
  - 说书+画面模式：直接生成（9:16 竖屏）
  - 剧集动画模式：直接生成（16:9 横屏）
- **生成方式**：每个片段/场景独立生成，使用分镜图作为起始帧

> **关于 extend 功能**：Veo 3.1 extend 功能仅用于延长单个片段/场景，
> 每次固定 +7 秒，不适合用于串联不同镜头。不同片段/场景之间使用 ffmpeg 拼接。

### 音频规范
- **BGM 自动禁止**：通过 `negative_prompt` API 参数自动排除背景音乐
- **后期配乐**：如需添加 BGM，使用 `/compose-video` 进行后期处理

### 脚本调用
- **Skill 内部脚本**：各 skill 的可执行脚本位于 `agent_runtime_profile/.claude/skills/{skill-name}/scripts/` 目录下
- **虚拟环境**：默认已激活，脚本无需手动激活 .venv

---

## 内容模式

系统支持两种内容模式，通过 `project.json` 中的 `content_mode` 字段切换：

| 维度 | 说书+画面模式（默认） | 剧集动画模式 |
|------|----------------------|-------------|
| content_mode | `narration` | `drama` |
| 内容形式 | 保留小说原文，不改编 | 小说改编为剧本 |
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

- `projects/` - 所有视频项目的工作空间
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
5. **人物设计**：`/generate-characters` 生成人物设计图（3:4 竖版）
6. **线索设计**：`/generate-clues` 生成线索设计图（16:9 横屏）
7. **分镜图片**：`/generate-storyboard` 直接生成分镜图
   - 直接生成单独场景图（**9:16 竖屏**）
   - 使用 character_sheet 和 clue_sheet 作为参考图保持一致性
8. **视频生成**：`/generate-video --episode N` 生成视频
   - **9:16 竖屏**格式
   - 每个片段独立生成，使用分镜图作为起始帧
   - 自动使用 ffmpeg 拼接成完整视频
   - 视频 Prompt 仅包含角色对话（如有），不包含旁白
   - 支持断点续传
9. **后期配音**：人工录制小说原文旁白
10. **后期合成**：`/compose-video` 合并视频、旁白、BGM

每个步骤都有审核点，可以在确认后再继续下一步。

## 工作流程（剧集动画模式）

如需使用剧集动画模式，在 `project.json` 中设置 `content_mode: "drama"`：

1. **准备小说**：将小说文本放入 `projects/{项目名}/source/`
2. **项目概述**：上传源文件后系统自动生成项目概述（synopsis、genre、theme、world_setting），供后续 Agent 参考
3. **生成剧本**：系统调用 `novel-to-storyboard-script` agent 将小说转为分镜剧本
4. **确认线索**：识别需要固化的重要物品和环境元素
5. **人物设计**：`/generate-characters` 生成人物设计图
6. **线索设计**：`/generate-clues` 生成线索设计图
7. **分镜图片**：`/generate-storyboard` 直接生成分镜图（16:9 横屏）
8. **视频生成**：`/generate-video --episode N` 生成视频（16:9 横屏）
9. **后期处理**（可选）：`/compose-video` 添加 BGM、片头片尾

## 视频生成模式

### 标准模式（推荐）

每个场景独立生成视频，然后拼接：

```bash
python agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py \
    my_project script.json --episode 1
```

### 断点续传

如果生成中断，可以从上次检查点继续：

```bash
python agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py \
    my_project script.json --episode 1 --resume
```

### 单场景模式

生成单个场景的视频（用于测试或重新生成）：

```bash
python agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py \
    my_project script.json --scene E1S1
```

### 分段标记

在剧本 JSON 中使用 `segment_break: true` 标记大的场景切换，用于后期添加转场效果：

```json
{
  "segment_id": "E1S05",
  "segment_break": true,
  "image_prompt": "...",
  "video_prompt": "..."
}
```

### 剧本核心字段

每个片段/场景包含以下核心字段：

| 字段 | 说明 |
|------|------|
| `segment_id` / `scene_id` | 唯一标识 |
| `novel_text` | 小说原文（仅说书模式，用于后期配音） |
| `image_prompt` | 分镜图生成 Prompt（直接用于 Gemini API） |
| `video_prompt` | 视频生成 Prompt（直接用于 Veo API） |
| `characters_in_segment/scene` | 出场人物列表 |
| `clues_in_segment/scene` | 重要线索列表 |
| `duration_seconds` | 时长（4/6/8 秒） |
| `transition_to_next` | 转场类型 |

## Veo 3.1 技术参考

| 功能 | 说明 |
|------|------|
| 图生视频 | 使用分镜图作为起始帧 |
| 单片段/场景时长 | 说书模式默认 4 秒，剧集动画模式默认 8 秒 |
| 时长选项 | 仅支持 4s / 6s / 8s |
| extend 功能 | 仅用于延长单个片段/场景，每次固定 +7 秒，最多延长至 148 秒 |
| 图片分辨率 | 1K |
| 视频分辨率 | 1080p |
| 宽高比 | 说书模式 9:16 竖屏，剧集动画模式 16:9 横屏（通过 API 参数设置） |

> 注意：extend 功能设计用于延长同一个片段/场景的动作，不适合用于串联不同镜头。

## 关键原则

- **人物一致性**：每个场景都使用分镜图作为起始帧，确保人物形象一致
- **线索一致性**：重要物品和环境元素通过 `clues` 机制固化，确保跨场景一致
- **分镜连贯性**：使用 segment_break 标记场景切换点，后期可添加转场效果
- **质量控制**：每个场景生成后检查质量，可单独重新生成不满意的场景

## 环境要求

- Python 3.10+
- Gemini API 密钥 或 Vertex AI 配置（通过 WebUI 配置页设置）
- ffmpeg（用于视频后期处理）

## API 后端配置

项目支持两种 Gemini API 后端，图片和视频可分别选择不同后端。
所有 API 相关配置（后端选择、API Key、速率限制、模型选择等）均通过 **WebUI 配置页**（`/settings`）管理，无需手动编辑 `.env` 文件。

### AI Studio（默认）

适合个人开发和测试。从 https://aistudio.google.com/apikey 获取 API 密钥，在配置页填入即可。

### Vertex AI

适合需要更高配额或企业使用。在配置页上传服务账号 JSON 凭证文件，然后切换后端即可。

## 项目目录结构

每个视频项目存放在 `projects/{项目名}/` 下：

```
projects/{项目名}/
├── project.json       # 项目级元数据（人物、线索、状态）
├── style_reference.png  # 风格参考图（可选，上传后自动生成 style_description）
├── source/            # 原始小说内容
├── scripts/           # 分镜剧本 (JSON)
├── characters/        # 人物设计图
├── clues/             # 线索设计图（重要物品/环境）
├── storyboards/       # 分镜图片
│   ├── scene_E1S01.png   # 分镜图（说书模式：9:16，剧集模式：16:9）
│   ├── scene_E1S02.png
│   └── ...
├── videos/            # 视频分镜（含 checkpoint 文件）
└── output/            # 最终输出
```

### project.json 结构

项目级元数据文件包含：
- `title`：项目标题
- `content_mode`：内容模式（`narration` 默认 或 `drama`）
- `style`：整体视觉风格标签（如 `Photographic`、`Anime`、`3D Animation`）
- `style_image`：风格参考图路径（可选，如 `style_reference.png`）
- `style_description`：AI 分析的风格描述（上传风格参考图后自动生成，可手动编辑）
- `overview`：**项目概述**（上传源文件后自动生成，包含故事梗概、题材类型、核心主题、世界观设定）
- `aspect_ratio`：可选，自定义各资源的画面比例
- `episodes`：剧集列表（仅包含核心元数据）
- `characters`：**人物完整定义**和设计图路径
- `clues`：**线索完整定义**和设计图路径

> **注意**：`status`、`episodes[].status`、`episodes[].scenes_count` 等统计字段已改为**读时计算**，
> 不再存储在 JSON 中。API 响应会自动注入这些计算字段。

#### 数据分层原则

| 文件 | 存储内容 | 示例 |
|------|---------|------|
| `project.json` | 项目概述 `overview` | `overview: {"synopsis": "...", "genre": "...", "theme": "...", "world_setting": "..."}` |
| `project.json` | 角色/线索的**完整定义** | `characters: {"姜月茴": {"description": "...", "character_sheet": "..."}}` |
| `project.json` | 剧集**核心元数据** | `episodes: [{"episode": 1, "title": "...", "script_file": "..."}]` |
| `segments[]/scenes[]` | 本片段/场景的角色/线索**名称列表** | `characters_in_segment: ["姜月茴"]` |

**原则**:
- 角色和线索的 description、character_sheet、voice_style 等属性**只存储在 project.json**
- 剧集的 `scenes_count`、`status`、`duration_seconds` 等统计字段由 **StatusCalculator 读时计算**
- `characters_in_episode` 和 `clues_in_episode` 由 API 从 segments/scenes 聚合，不再存储

#### 写时同步 vs 读时计算

| 字段 | 存储方式 | 说明 |
|------|---------|------|
| `episodes[].episode` | 写时同步 | 剧本保存时自动同步 |
| `episodes[].title` | 写时同步 | 剧本保存时自动同步 |
| `episodes[].script_file` | 写时同步 | 剧本保存时自动同步 |
| `episodes[].scenes_count` | 读时计算 | API 响应时由 StatusCalculator 注入 |
| `episodes[].status` | 读时计算 | API 响应时由 StatusCalculator 注入 |
| `status.progress` | 读时计算 | API 响应时由 StatusCalculator 注入 |
| `status.current_phase` | 读时计算 | API 响应时由 StatusCalculator 注入 |
| `characters_in_episode` | 读时计算 | API 响应时从 segments/scenes 聚合 |
| `clues_in_episode` | 读时计算 | API 响应时从 segments/scenes 聚合 |

#### 完整示例

```json
{
  "title": "重生之皇后威武",
  "content_mode": "narration",
  "style": "Photographic",
  "style_image": "style_reference.png",
  "style_description": "Soft diffused lighting, muted earth tones with jade accents, photorealistic rendering, shallow depth of field, cinematic composition, traditional Chinese palace aesthetic",
  "overview": {
    "synopsis": "讲述姜月茴重生后，从受辱皇后逆袭成为权倾朝野的故事...",
    "genre": "古装宫斗、重生复仇",
    "theme": "复仇与救赎、女性觉醒",
    "world_setting": "架空古代皇朝，以后宫和朝堂为主要场景...",
    "generated_at": "2025-01-27T10:00:00Z"
  },
  "aspect_ratio": {
    "characters": "3:4",
    "clues": "16:9",
    "storyboard": "9:16",
    "video": "9:16"
  },
  "episodes": [
    {
      "episode": 1,
      "title": "襁褓惊变",
      "script_file": "scripts/episode_1.json"
    }
  ],
  "characters": {
    "姜月茴": {
      "description": "二十出头女子，鹅蛋脸，柳叶眉，杏眼明亮有神...",
      "character_sheet": "characters/姜月茴.png",
      "voice_style": "温柔但有威严，生气时冷冽如冰"
    },
    "裴与": {
      "description": "二十七八岁男子，身高八尺，剑眉星目...",
      "character_sheet": "characters/裴与.png",
      "voice_style": "初期意气风发，后期嘶哑绝望"
    }
  },
  "clues": {
    "玉佩": {
      "type": "prop",
      "description": "翠绿色祖传玉佩，雕刻着莲花纹样",
      "importance": "major",
      "clue_sheet": "clues/玉佩.png"
    }
  },
  "metadata": {
    "created_at": "2025-01-27T10:00:00Z",
    "updated_at": "2025-01-27T12:00:00Z"
  }
}
```

### 线索数据结构

```json
{
  "clues": {
    "玉佩": {
      "type": "prop",
      "description": "翠绿色祖传玉佩，雕刻着莲花纹样",
      "importance": "major",
      "clue_sheet": "clues/玉佩.png"
    },
    "老槐树": {
      "type": "location",
      "description": "村口百年老槐树，树干粗壮，有雷击痕迹",
      "importance": "minor",
      "clue_sheet": ""
    }
  }
}
```

- **type**：`prop`（道具）或 `location`（环境）
- **importance**：`major`（生成设计图）或 `minor`（仅描述）

### 在场景中使用线索

在剧本的场景中添加 `clues_in_scene` 或 `clues_in_segment` 字段：

```json
{
  "segment_id": "E1S03",
  "characters_in_segment": ["姜月茴"],
  "clues_in_segment": ["玉佩", "老槐树"],
  "image_prompt": "姜府后花园，百年老槐树下。中景镜头，姜月茴穿着淡青色绣花罗裙，手中握着翠绿色玉佩，若有所思地望向远方。午后斜阳从树叶间洒落，营造出怀旧伤感的氛围。",
  "video_prompt": "中景镜头，姜府后花园。姜月茴站在老槐树下，轻轻摩挲手中的玉佩，眼神中带着回忆。微风吹动衣袂，树叶沙沙作响。镜头静态，午后斜阳光线，怀旧伤感的氛围。"
}
```

生成分镜时，线索参考图会自动加入 API 调用。

## API 使用

图片和视频生成通过 Gemini API：
- 图片生成：`gemini-3.1-flash-image-preview`
- 视频生成：`veo-3.1-generate-001`
- 视频扩展：`veo-3.1-generate-001`（使用 extend 功能）

后端选择通过 WebUI 配置页管理（图片和视频可分别选择不同后端）。
